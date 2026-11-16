"""The daemon, which shares one pronsole process through a Unix socket, and the client call.

Protocol: a client sends one JSON line, {"command": "<pronsole command>"}, and gets
one back: {"ok": true, "output": [lines]} or {"ok": false, "error": "<reason>"}.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCKET = "pronsoled.sock"
ONLINE_TIMEOUT = 30  # seconds the printer gets to answer after `connect`


class DaemonError(RuntimeError):
    """An expected failure, reported to the user without a traceback."""


class Pronsole:
    """A pronsole child process. Everything it prints is echoed to our stdout, the log."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).with_name("_pronsole.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.lost = ""  # what pronsole said when the printer went away
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        for line in self.process.stdout:
            line = line.rstrip()
            if line:  # pronsole starts printer messages with \r, which reads as an empty line
                print(line, flush=True)
                self.lines.put(line)
            if "(disconnected?)" in line:
                self.lost = line

    def check(self) -> None:
        """Raise if pronsole has exited or lost the printer: it does not reconnect by itself.

        The reader thread ends when pronsole does, once all its output is echoed.
        """
        if not self.reader.is_alive():
            raise DaemonError(f"pronsole exited unexpectedly with code {self.process.wait()}")
        if self.lost:
            raise DaemonError(f"lost the printer: {self.lost}")

    def send(self, command: str) -> None:
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise DaemonError("pronsole is not running") from exc

    def connect(self, port: str, baud: int) -> None:
        """Connect to the printer and wait until it is online."""
        self.send(f"connect {port} {baud}")
        deadline = time.monotonic() + ONLINE_TIMEOUT
        while time.monotonic() < deadline:
            self.check()
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if "Printer is now online" in line:
                return
            if "Connection error" in line:
                raise DaemonError(line)
        raise DaemonError(f"the printer on {port} did not come online within {ONLINE_TIMEOUT} seconds")

    def execute(self, command: str) -> list[str]:
        """Send one command and return the lines pronsole prints in reply.

        pronsole does not mark the end of a reply, so it ends when pronsole has
        been quiet for 0.1 s, has printed nothing at all for 1 s, or after 5 s.
        """
        while not self.lines.empty():
            self.lines.get()  # printed before this command: not part of its reply
        self.send(command)
        reply: list[str] = []
        deadline = time.monotonic() + 5
        try:
            reply.append(self.lines.get(timeout=1))
            while time.monotonic() < deadline:
                reply.append(self.lines.get(timeout=0.1))
        except queue.Empty:
            pass
        return reply

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait()


def notify_ready() -> None:
    """Tell systemd (Type=notify) that clients can connect. Does nothing outside systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.sendto(b"READY=1", "\0" + address[1:] if address.startswith("@") else address)


def listening(path: Path) -> bool:
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(str(path))
        except OSError:
            return False
        return True


def answer(connection: socket.socket, pronsole: Pronsole) -> None:
    """Read one request from a client, run it and send the reply."""
    connection.settimeout(5)
    try:
        command = json.loads(connection.makefile().readline())["command"]
        reply = {"ok": True, "output": pronsole.execute(command)}
    except DaemonError as exc:
        reply = {"ok": False, "error": str(exc)}
    except (OSError, ValueError, LookupError, TypeError) as exc:
        reply = {"ok": False, "error": f"bad request: {exc!r}"}
    try:
        connection.sendall(json.dumps(reply).encode() + b"\n")
    except OSError:
        pass  # the client has gone away


def serve(directory: Path, port: str, baud: int) -> None:
    """Run the daemon until interrupted. Raises DaemonError if pronsole or the printer fails."""
    path = directory / SOCKET
    directory.mkdir(parents=True, exist_ok=True)
    if listening(path):
        raise DaemonError(f"a daemon is already running on {path}")
    path.unlink(missing_ok=True)  # left behind by a daemon that was killed

    pronsole = Pronsole()
    try:
        pronsole.connect(port, baud)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            path.chmod(0o660)  # members of the daemon's group may connect
            server.listen()
            server.settimeout(0.5)
            notify_ready()
            print(f"Ready: listening on {path}", flush=True)
            while True:
                pronsole.check()  # exit when pronsole is of no use, so that systemd restarts us
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    answer(connection, pronsole)
    finally:
        pronsole.stop()
        path.unlink(missing_ok=True)


def request(directory: Path, command: str) -> list[str]:
    """Run one pronsole command through the daemon and return its reply lines."""
    path = directory / SOCKET
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(30)
            connection.connect(str(path))
            connection.sendall(json.dumps({"command": command}).encode() + b"\n")
            reply = json.loads(connection.makefile().readline())
    except PermissionError as exc:
        raise DaemonError(
            f"permission denied on {path}. Add yourself to the pronsoled group "
            "(`sudo usermod -aG pronsoled $USER`), then log out and back in."
        ) from exc
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise DaemonError(
            f"daemon is not running: no socket at {path}. Start it with "
            "`sudo systemctl start pronsoled` (if that fails, see `journalctl -u pronsoled`) "
            "or by hand with `pronsoled start`."
        ) from exc
    except (ValueError, ConnectionError) as exc:
        raise DaemonError(f"the daemon at {path} closed the connection without answering") from exc
    if not reply["ok"]:
        raise DaemonError(reply["error"])
    return reply["output"]
