from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .daemon import DaemonError, DaemonServer, RuntimePaths, StartupError, send_request


def default_config_dir() -> Path:
    return Path("/var/lib/pronsoled")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="pronsoled")
    command.add_argument(
        "--config-dir",
        type=Path,
        default=Path(os.environ.get("PRONSOLED_CONFIG_DIR", default_config_dir())),
        help="directory for setup records, socket, PID, and logs",
    )
    
    subparsers = command.add_subparsers(dest="action", required=True)
    
    start = subparsers.add_parser("start", help="start the persistent pronsole daemon")
    start.add_argument("port", nargs="?", default="auto")
    start.add_argument("baud", nargs="?", default=115200, type=int)
    start.add_argument("--pronsole", default="/usr/local/bin/pronsole.py", help="pronsole executable")
    
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


def run(args: argparse.Namespace) -> int:
    paths = RuntimePaths(args.config_dir.expanduser())
    
    if args.action == "start":
            port = find_port() if args.port == "auto" else args.port
            server = DaemonServer(paths, port, args.baud, args.pronsole)
            
            print(f"Starting pronsoled for {port} @ {args.baud}")
            print(f"Setup: {paths.setup}")
            
            server.run()
        
            return 0

    elif args.action == "status":
        print("\n".join(send_request(paths, "eta")))

    elif args.action == "send":
        print("\n".join(send_request(paths, args.send_cmd)))

    elif args.action == "print":
        if not args.gcode_file.is_file():
            raise DaemonError(f"G-code file does not exist: {args.gcode_file}")
        status = "\n".join(send_request(paths, "eta"))

        if "not currently printing" not in status.lower():
            raise DaemonError(f"printer is busy; status response was: {status or '<empty>'}")
            
        send_request(paths, f"load {args.gcode_file.resolve()}")
        send_request(paths, "print")
            
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