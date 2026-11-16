"""Command line: `pronsoled start` runs the daemon, every other command talks to it."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
from pathlib import Path

from . import __version__
from .daemon import SOCKET, DaemonError, request, serve

# Where the systemd service keeps its socket while it runs (see pronsoled.service).
SYSTEM_DIR = Path("/run/pronsoled")
JOB = "job.gcode"


def socket_dir(daemon: bool) -> Path:
    """The directory holding the socket, unless --config-dir names one.

    A daemon started by hand uses a per-user directory. Clients talk to the
    systemd service, or to a hand-started daemon when the service is not running.
    """
    user_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".local" / "state") / "pronsoled"
    if daemon or (not SYSTEM_DIR.is_dir() and os.path.exists(user_dir / SOCKET)):
        return user_dir
    return SYSTEM_DIR


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="pronsoled")
    command.add_argument("--version", action="version", version=f"pronsoled {__version__}")
    command.add_argument(
        "--config-dir",
        type=Path,
        default=os.environ.get("PRONSOLED_CONFIG_DIR") or None,
        help="directory holding the socket (default: $PRONSOLED_CONFIG_DIR, "
        "/run/pronsoled, or $XDG_RUNTIME_DIR/pronsoled)",
    )
    subparsers = command.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start", help="run the daemon in the foreground")
    start.add_argument("port", nargs="?", default="auto", help="serial port (default: the only /dev/ttyACM*)")
    start.add_argument("baud", nargs="?", default=115200, type=int)

    subparsers.add_parser("status", help="show print progress")
    subparsers.add_parser("send", help="send one pronsole command or G-code").add_argument("send_cmd")
    subparsers.add_parser("print", help="start printing a G-code file").add_argument("gcode_file", type=Path)
    subparsers.add_parser("abort", help="pause the current print")
    return command


def find_port(dev: Path = Path("/dev")) -> str:
    ports = sorted(str(port) for port in dev.glob("ttyACM*"))
    if len(ports) != 1:
        found = ", ".join(ports) or "none"
        raise DaemonError(f"expected exactly one {dev}/ttyACM* port but found {found}; name the port explicitly")
    return ports[0]


def stage_job(directory: Path, gcode_file: Path) -> Path:
    """Copy the job next to the socket, where a daemon running as another user can read it.

    Home directories are usually private (0750 on Ubuntu), so the service cannot
    open files there.
    """
    job = directory / JOB
    job.unlink(missing_ok=True)  # may belong to another user
    shutil.copyfile(gcode_file, job)
    job.chmod(0o640)  # whatever the umask: the daemon reads it through its group
    return job


def run(args: argparse.Namespace) -> None:
    directory = args.config_dir or socket_dir(daemon=args.action == "start")

    def ask(command: str) -> str:
        return "\n".join(request(directory, command))

    if args.action == "start":
        port = find_port() if args.port == "auto" else args.port
        print(f"Starting pronsoled for {port} @ {args.baud}", flush=True)
        signal.signal(signal.SIGTERM, signal.default_int_handler)  # `systemctl stop` acts like Ctrl-C
        try:
            serve(directory, port, args.baud)
        except KeyboardInterrupt:
            print("Stopped", flush=True)

    elif args.action == "status":
        print(ask("eta"))

    elif args.action == "send":
        print(ask(args.send_cmd))

    elif args.action == "print":
        if not args.gcode_file.is_file():
            raise DaemonError(f"G-code file does not exist: {args.gcode_file}")
        status = ask("eta")
        if "not currently printing" not in status:
            raise DaemonError(f"the printer is busy: {status or 'no answer'}")
        for command in (f"load {stage_job(directory, args.gcode_file)}", "print"):
            reply = ask(command)
            if any(sign in reply for sign in ("File not found", "Traceback", "No file loaded", "Not connected")):
                raise DaemonError(f"`{command}` failed: {reply}")
        print(f"Print job started: {args.gcode_file}")

    elif args.action == "abort":
        print(ask("pause") or "Print paused")


def main() -> int:
    args = parser().parse_args()
    try:
        run(args)
    except (DaemonError, OSError) as exc:
        print(f"pronsoled: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
