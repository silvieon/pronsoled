from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
from pathlib import Path

from .daemon import (
    SYSTEM_RUNTIME_DIR,
    SYSTEM_UNIT,
    DaemonError,
    DaemonServer,
    RuntimePaths,
    Shutdown,
    StartupError,
    check_pronsole,
    find_pronsole,
    send_request,
)


def user_config_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "pronsoled"
    return Path.home() / ".local" / "state" / "pronsoled"


def daemon_config_dir() -> Path:
    """Where `pronsoled start` puts its socket when no directory is given."""
    env = os.environ.get("PRONSOLED_CONFIG_DIR")
    if env:
        return Path(env)
    if SYSTEM_RUNTIME_DIR.is_dir() and os.access(SYSTEM_RUNTIME_DIR, os.W_OK):
        return SYSTEM_RUNTIME_DIR
    return user_config_dir()


def client_config_dir() -> Path:
    """Find the running daemon: the system service first, then one started by hand."""
    env = os.environ.get("PRONSOLED_CONFIG_DIR")
    if env:
        return Path(env)
    candidates = [user_config_dir()]
    if SYSTEM_UNIT.exists() or SYSTEM_RUNTIME_DIR.is_dir():
        candidates.insert(0, SYSTEM_RUNTIME_DIR)
    for candidate in candidates:
        try:
            if RuntimePaths(candidate).socket.exists():
                return candidate
        except OSError:  # e.g. /run/pronsoled exists but we are not in its group
            return candidate
    return candidates[0]


def _stop_on_signal(signum: int, _frame: object) -> None:
    # Ignore repeats so cleanup is not interrupted, then unwind run().
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise Shutdown(signal.Signals(signum).name)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="pronsoled")
    command.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="directory for the socket, PID, setup record and log "
        "(default: $PRONSOLED_CONFIG_DIR, /run/pronsoled, or $XDG_RUNTIME_DIR/pronsoled)",
    )

    subparsers = command.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start", help="start the persistent pronsole daemon")
    start.add_argument("port", nargs="?", default="auto")
    start.add_argument("baud", nargs="?", default=115200, type=int)
    start.add_argument("--pronsole", default=None, help="pronsole executable (default: found on PATH)")
    start.add_argument("--log-file", type=Path, default=None, help="pronsole output log")

    check = subparsers.add_parser("check", help="verify that pronsole can be started")
    check.add_argument("--pronsole", default=None, help="pronsole executable (default: found on PATH)")

    subparsers.add_parser("status", help="ask the printer for its current status")

    send = subparsers.add_parser("send", help="send one raw pronsole command")
    send.add_argument("send_cmd")

    print_command = subparsers.add_parser("print", help="start a G-code print")
    print_command.add_argument("gcode_file", type=Path)

    subparsers.add_parser("abort", help="pause the current print")

    return command


def find_port() -> str:
    ports = sorted(Path("/dev").glob("ttyACM*"))
    if not ports:
        raise DaemonError("no /dev/ttyACM* printer was found; specify the port explicitly")
    if len(ports) > 1:
        names = ", ".join(str(port) for port in ports)
        raise DaemonError(f"multiple printer ports found ({names}); specify one explicitly")
    return str(ports[0])


def stage_job(paths: RuntimePaths, gcode_file: Path) -> Path:
    """Copy the job next to the socket so a daemon running as another user can read it.

    Home directories are usually private (0750 on Ubuntu), so the system service
    cannot open files there. Falls back to the original path if the copy fails.
    """
    staging = paths.job.with_suffix(".tmp")
    try:
        shutil.copyfile(gcode_file, staging)
        # Give the copy the runtime directory's group (the daemon's), not ours.
        try:
            os.chown(staging, -1, paths.config_dir.stat().st_gid)
        except OSError:
            pass
        os.chmod(staging, 0o660)
        os.replace(staging, paths.job)
    except OSError:
        return gcode_file.resolve()
    return paths.job


def run(args: argparse.Namespace) -> int:
    if args.action == "start":
        config_dir = (args.config_dir or daemon_config_dir()).expanduser()
        log_file = args.log_file.expanduser() if args.log_file else None
        paths = RuntimePaths(config_dir, log_file)
        port = find_port() if args.port == "auto" else args.port
        server = DaemonServer(paths, port, args.baud, args.pronsole or find_pronsole())
        print(f"Starting pronsoled for {port} @ {args.baud}", flush=True)
        signal.signal(signal.SIGTERM, _stop_on_signal)
        signal.signal(signal.SIGINT, _stop_on_signal)
        try:
            server.run()
        except Shutdown as exc:
            print(f"pronsoled: stopped ({exc})", flush=True)
        return 0

    if args.action == "check":
        executable = args.pronsole or find_pronsole()
        ok, output = check_pronsole(executable)
        print(f"pronsole: {executable} ({sys.executable})")
        if not ok:
            raise DaemonError(
                f"pronsole does not start:\n{output}\n"
                "Install Printrun into pronsoled's environment with `sudo ./install.sh --with-printrun`, "
                "or pass --pronsole /path/to/pronsole.py."
            )
        print("pronsole starts OK")
        return 0

    paths = RuntimePaths((args.config_dir or client_config_dir()).expanduser())

    if args.action == "status":
        print("\n".join(send_request(paths, "eta")))

    elif args.action == "send":
        print("\n".join(send_request(paths, args.send_cmd)))

    elif args.action == "print":
        if not args.gcode_file.is_file():
            raise DaemonError(f"G-code file does not exist: {args.gcode_file}")
        status = "\n".join(send_request(paths, "eta"))
        if "not currently printing" not in status.lower():
            raise DaemonError(f"printer is busy; status response was: {status or '<empty>'}")
        loaded = "\n".join(send_request(paths, f"load {stage_job(paths, args.gcode_file)}"))
        if any(marker in loaded for marker in ("Traceback", "Error", "No such file")):
            raise DaemonError(f"pronsole could not load {args.gcode_file}:\n{loaded}")
        started = "\n".join(send_request(paths, "print"))
        if "not connected" in started.lower() or "not online" in started.lower():
            raise DaemonError(f"printer is not connected; pronsole said: {started}")
        print(f"Print job started: {args.gcode_file}")

    elif args.action == "abort":
        send_request(paths, "pause")
        print("Print paused")

    return 0


def main() -> int:
    try:
        return run(parser().parse_args())
    except StartupError as exc:
        print("pronsoled: unable to start daemon", file=sys.stderr)
        for cause in exc.causes:
            print(f"  - {cause}", file=sys.stderr)
        return 1
    except DaemonError as exc:
        print(f"pronsoled: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\npronsoled: stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
