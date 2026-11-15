from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
from pathlib import Path

from .daemon import DaemonError, DaemonServer, RuntimePaths, StartupError, pronsole_command, send_request

# Where the systemd service keeps its socket while it runs (see pronsoled.service).
SYSTEM_DIR = Path("/run/pronsoled")


def default_dir(daemon: bool) -> Path:
    """The directory holding the socket, unless --config-dir names one.

    A daemon started by hand uses a per-user directory. Clients talk to the
    systemd service if it is running, otherwise to a hand-started daemon.
    """
    env = os.environ.get("PRONSOLED_CONFIG_DIR")
    if env:
        return Path(env)
    user_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".local" / "state") / "pronsoled"
    if daemon or (not SYSTEM_DIR.is_dir() and RuntimePaths(user_dir).socket.exists()):
        return user_dir
    return SYSTEM_DIR


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
    start.add_argument("--pronsole", default=None, help="pronsole executable to use instead of the bundled launcher")
    start.add_argument("--log-file", type=Path, default=None, help="pronsole output log")

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
    try:
        paths.job.unlink(missing_ok=True)
        shutil.copyfile(gcode_file, paths.job)
        os.chmod(paths.job, 0o660)
    except OSError:
        return gcode_file.resolve()
    return paths.job


def run(args: argparse.Namespace) -> int:
    if args.action == "start":
        paths = RuntimePaths((args.config_dir or default_dir(daemon=True)).expanduser(), args.log_file)
        port = find_port() if args.port == "auto" else args.port
        print(f"Starting pronsoled for {port} @ {args.baud}", flush=True)
        signal.signal(signal.SIGTERM, signal.default_int_handler)  # systemd stop = Ctrl-C
        DaemonServer(paths, port, args.baud, pronsole_command(args.pronsole)).run()
        return 0

    paths = RuntimePaths((args.config_dir or default_dir(daemon=False)).expanduser())

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
    except KeyboardInterrupt:  # Ctrl-C or SIGTERM: a normal stop
        print("pronsoled: stopped", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
