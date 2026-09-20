from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DaemonError(RuntimeError):
    """An expected daemon or printer error."""


class StartupError(DaemonError):
    """One or more startup checks failed."""

    def __init__(self, causes: list[str]):
        self.causes = causes
        super().__init__("; ".join(causes))


@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path

    @property
    def socket(self) -> Path:
        return self.config_dir / "pronsoled.sock"

    @property
    def pid(self) -> Path:
        return self.config_dir / "pronsoled.pid"

    @property
    def log(self) -> Path:
        return self.config_dir / "pronsoled.log"

    @property
    def setup(self) -> Path:
        return self.config_dir / "config"


class PronsoleProcess:
    def __init__(self, paths: RuntimePaths, port: str, baud: int, executable: str):
        self.paths = paths
        self.port = port
        self.baud = baud
        self.executable = executable
        self.process: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None
        self.log_handle: Any = None
        self.command_lock = threading.Lock()

    def start(self) -> None:
        self.log_handle = self.paths.log.open("a", encoding="utf-8", buffering=1)
        command = [self.executable]
        if self.executable.endswith(".py"):
            command = [sys.executable, self.executable]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.log_handle.close()
            raise DaemonError(
                f"could not start {self.executable!r}: {exc}. "
                "Install Printrun and ensure pronsole.py is on PATH."
            ) from exc

        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()
        self.execute(f"connect {self.port} {self.baud}", timeout=3.0)

    def self_test(self) -> dict[str, tuple[bool, str]]:
        probes = {
            "status": "eta",
            "send": "M105",
            "print": "help print",
            "abort": "help pause",
        }
        results: dict[str, tuple[bool, str]] = {}
        for name, command in probes.items():
            try:
                time.sleep(1)
                output = self.execute(command, timeout=3.0)
            except DaemonError as exc:
                results[name] = (False, str(exc))
                continue
            if output and any(line.strip() for line in output):
                results[name] = (True, f"{command} responded")
            else:
                results[name] = (False, f"no response to {command!r}")
        return results

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.log_handle.write(line)
            self.responses.put(line.rstrip())

    def execute(self, command: str, timeout: float = 1.0) -> list[str]:
        with self.command_lock:
            if self.process is None or self.process.poll() is not None:
                raise DaemonError("the pronsole process is not running")
            assert self.process.stdin is not None
            while True:
                try:
                    self.responses.get_nowait()
                except queue.Empty:
                    break
            try:
                self.process.stdin.write(command.rstrip("\n") + "\n")
                self.process.stdin.flush()
            except OSError as exc:
                raise DaemonError(f"could not send command {command!r}: {exc}") from exc

            lines: list[str] = []
            deadline = time.monotonic() + timeout
            quiet_deadline: float | None = None
            while time.monotonic() < deadline:
                wait_for = 0.05
                if quiet_deadline is not None:
                    wait_for = min(wait_for, max(0, quiet_deadline - time.monotonic()))
                    if wait_for == 0:
                        break
                try:
                    lines.append(self.responses.get(timeout=wait_for))
                    quiet_deadline = time.monotonic() + 0.1
                except queue.Empty:
                    if quiet_deadline is not None:
                        break
            return lines

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log_handle is not None:
            self.log_handle.close()


class DaemonServer:
    def __init__(self, paths: RuntimePaths, port: str, baud: int, executable: str):
        self.paths = paths
        self.pronsole = PronsoleProcess(paths, port, baud, executable)
        self.server: socket.socket | None = None
        self.stop_event = threading.Event()

    def run(self) -> None:
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        if self.paths.socket.exists():
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.connect(str(self.paths.socket))
                raise DaemonError(f"daemon is already running ({self.paths.socket})")
            except ConnectionRefusedError:
                self.paths.socket.unlink()

        try:
            self.pronsole.start()
            self_test = self.pronsole.self_test()
            print("Startup command self-test:", flush=True)
            for name, (passed, reason) in self_test.items():
                label = "PASS" if passed else "FAIL"
                print(f"  [{label}] {name}: {reason}", flush=True)
            failures = [f"command '{name}' failed: {reason}" for name, (passed, reason) in self_test.items() if not passed]
            if failures:
                raise StartupError(failures)
            self.paths.pid.write_text(f"{os.getpid()}\n", encoding="utf-8")
            self.paths.setup.write_text(
                json.dumps(
                    {
                        "port": self.pronsole.port,
                        "baud": self.pronsole.baud,
                        "pronsole": self.pronsole.executable,
                        "pid": os.getpid(),
                        "socket": str(self.paths.socket),
                        "log": str(self.paths.log),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(str(self.paths.socket))
            self.server.listen(8)
            self.server.settimeout(0.5)
            while not self.stop_event.is_set():
                try:
                    connection, _ = self.server.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_client, args=(connection,), daemon=True
                ).start()
        finally:
            self.stop()

    def _handle_client(self, connection: socket.socket) -> None:
        with connection:
            try:
                request = json.loads(connection.makefile().readline())
                output = self.pronsole.execute(request["command"])
                payload = {"ok": True, "output": output}
            except (DaemonError, KeyError, json.JSONDecodeError) as exc:
                payload = {"ok": False, "error": str(exc)}
            connection.sendall((json.dumps(payload) + "\n").encode())

    def stop(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            self.server.close()
        self.pronsole.stop()
        for path in (self.paths.socket, self.paths.pid):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def send_request(paths: RuntimePaths, command: str) -> list[str]:
    if not paths.socket.exists():
        raise DaemonError(
            f"daemon is not running: socket not found at {paths.socket}. "
            "Start it with `pronsoled start`."
        )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(paths.socket))
            connection.sendall((json.dumps({"command": command}) + "\n").encode())
            response = json.loads(connection.makefile().readline())
    except OSError as exc:
        raise DaemonError(f"could not contact daemon at {paths.socket}: {exc}") from exc
    if not response.get("ok"):
        raise DaemonError(response.get("error", "daemon rejected the command"))
    return response.get("output", [])