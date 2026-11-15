from __future__ import annotations

import collections
import importlib.util
import json
import os
import queue
import shutil
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


class Shutdown(Exception):
    """Raised by the SIGTERM/SIGINT handler to stop the daemon cleanly."""


# Members of the daemon's group may connect; everyone else is refused.
SOCKET_MODE = 0o660

# pronsole output that means `connect` did not reach the printer.
SYSTEM_UNIT = Path("/etc/systemd/system/pronsoled.service")
SYSTEM_RUNTIME_DIR = Path("/run/pronsoled")

CONNECT_FAILURES = ("could not connect", "connection error", "not connected")


def sd_notify(message: str) -> None:
    """Tell systemd about our state (Type=notify). No-op outside systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.connect(address)
            notify.sendall(message.encode())
    except OSError:
        pass


def find_pronsole() -> str:
    """Locate a working Printrun pronsole.

    1. Printrun importable by this interpreter (pip into pronsoled's venv, or the
       distro's python3 package seen through --system-site-packages): run it
       through our own launcher.
    2. Otherwise a pronsole script: `pronsole.py` (pip) or `pronsole` (apt), next to
       this interpreter or on PATH. Scripts whose Python cannot import printrun,
       e.g. a stale pip copy shadowing apt's /usr/bin/pronsole, are skipped.
    """
    if importlib.util.find_spec("printrun") is not None:
        return str(Path(__file__).with_name("_pronsole.py"))
    directories = [str(Path(sys.executable).parent), *os.environ.get("PATH", "").split(os.pathsep), "/usr/bin"]
    candidates: list[str] = []
    for directory in directories:
        for name in ("pronsole.py", "pronsole"):
            found = shutil.which(name, path=directory)
            if found and found not in candidates:
                candidates.append(found)
    for candidate in candidates:
        if _imports_printrun(candidate):
            return candidate
    # Nothing works; return the first match so `pronsoled check` can show why.
    return candidates[0] if candidates else "pronsole"


def _interpreter(script: str) -> list[str]:
    """The command a script's shebang names (or ours, if it has none)."""
    try:
        with open(script, "rb") as handle:
            first = handle.readline().decode(errors="replace").strip()
    except OSError:
        return [sys.executable]
    if first.startswith("#!") and "python" in first:
        return first[2:].split()
    return [sys.executable]


def _imports_printrun(script: str) -> bool:
    try:
        result = subprocess.run(
            [*_interpreter(script), "-c", "import printrun"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def pronsole_command(executable: str) -> list[str]:
    """Run executables directly so their own shebang (and Python) is used."""
    if executable.endswith(".py") and not _has_shebang(executable):
        return [sys.executable, executable]
    return [executable]


def check_pronsole(executable: str, timeout: float = 60) -> tuple[bool, str]:
    """Start pronsole as the daemon would, ask it to exit, and report whether that worked."""
    try:
        result = subprocess.run(
            pronsole_command(executable),
            input="exit\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    broken = result.returncode != 0 or "Traceback" in output or "No module named" in output
    return not broken, output


def _has_shebang(path: str) -> bool:
    """True if the file can be executed directly with its own interpreter line."""
    if not os.access(path, os.X_OK):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path
    log_file: Path | None = None

    @property
    def socket(self) -> Path:
        return self.config_dir / "pronsoled.sock"

    @property
    def pid(self) -> Path:
        return self.config_dir / "pronsoled.pid"

    @property
    def log(self) -> Path:
        return self.log_file or self.config_dir / "pronsoled.log"

    @property
    def job(self) -> Path:
        return self.config_dir / "job.gcode"

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
        # Last lines pronsole printed, reported if it dies unexpectedly.
        self.tail: collections.deque[str] = collections.deque(maxlen=30)
        self.exited = threading.Event()
        self.stopping = False

    def start(self) -> None:
        self.paths.log.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.paths.log.open("a", encoding="utf-8", buffering=1)
        # pronsole expects its config directory to exist.
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        try:
            (config_home / "Printrun").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            self.process = subprocess.Popen(
                pronsole_command(self.executable),
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
        output = "\n".join(self.execute(f"connect {self.port} {self.baud}", timeout=3.0))
        if self.exited.wait(0.2):
            raise StartupError([self.death_report()])
        errors = [line.strip() for line in output.splitlines()
                  if any(failure in line.lower() for failure in CONNECT_FAILURES)]
        if errors:
            raise StartupError([f"could not connect to {self.port}: {' '.join(errors)}"])

    def self_test(self) -> dict[str, tuple[bool, str]]:
        probes = {
            "status": "eta",
            "send": "M105",
            "print": "help print",
            "abort": "help pause",
        }
        results: dict[str, tuple[bool, str]] = {}
        for name, command in probes.items():
            if self.exited.is_set():
                break
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
            self.tail.append(line.rstrip())
            self.responses.put(line.rstrip())
        self.exited.set()

    def death_report(self) -> str:
        code = None
        if self.process is not None:
            try:
                code = self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        lines = "\n".join(f"    {line}" for line in self.tail) or "    <no output>"
        return (
            f"pronsole ({self.executable}) exited unexpectedly with code {code}. "
            f"Last output (full log: {self.paths.log}):\n{lines}"
        )

    def execute(self, command: str, timeout: float = 1.0) -> list[str]:
        with self.command_lock:
            if self.process is None:
                raise DaemonError("the pronsole process has not been started")
            if self.process.poll() is not None:
                raise DaemonError(self.death_report())
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
        self.stopping = True
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
            if self.pronsole.exited.is_set():
                raise StartupError([self.pronsole.death_report()])
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
            os.chmod(self.paths.socket, SOCKET_MODE)
            self.server.listen(8)
            self.server.settimeout(0.5)
            # Only now can clients connect: let `systemctl start` return.
            sd_notify(f"READY=1\nSTATUS=Serving {self.pronsole.port} on {self.paths.socket}")
            print(f"Ready: listening on {self.paths.socket}", flush=True)
            while not self.stop_event.is_set():
                # Without pronsole every request would fail: exit so systemd restarts us.
                if self.pronsole.exited.is_set():
                    raise DaemonError(self.pronsole.death_report())
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
        sd_notify("STOPPING=1")
        self.stop_event.set()
        if self.server is not None:
            self.server.close()
        self.pronsole.stop()
        for path in (self.paths.socket, self.paths.pid, self.paths.job):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def send_request(paths: RuntimePaths, command: str) -> list[str]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(paths.socket))
            connection.sendall((json.dumps({"command": command}) + "\n").encode())
            response = json.loads(connection.makefile().readline())
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        if SYSTEM_UNIT.exists():
            hint = (
                "Start it with `sudo systemctl start pronsoled`; if it keeps failing, "
                "see `systemctl status pronsoled` and `journalctl -u pronsoled`."
            )
        else:
            hint = "Start it with `pronsoled start` (or `systemctl --user start pronsoled`)."
        raise DaemonError(f"daemon is not running: no socket at {paths.socket}. {hint}") from exc
    except PermissionError as exc:
        raise DaemonError(
            f"permission denied on {paths.socket}. Add yourself to the pronsoled group "
            "(`sudo usermod -aG pronsoled $USER`), then log out and back in."
        ) from exc
    except OSError as exc:
        raise DaemonError(f"could not contact daemon at {paths.socket}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DaemonError(f"invalid response from daemon at {paths.socket}") from exc
    if not response.get("ok"):
        raise DaemonError(response.get("error", "daemon rejected the command"))
    return response.get("output", [])