"""Shared test helpers: run a daemon in a subprocess and the command line against it."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT =Path(__file__).resolve().parent.parent
FAKES = ROOT / "tests" / "fakes"
CLI = [sys.executable, "-m", "pronsoled.cli"]

sys.path.insert(0, str(ROOT))  # so that the tests import pronsoled from this checkout

IDLE = "Printer is not currently printing. No ETA available."

HAVE_PRINTRUN = importlib.util.find_spec("printrun") is not None
needs_printrun = unittest.skipUnless(
    HAVE_PRINTRUN,
    "Printrun is not installed for this Python (after ./install.sh, run the tests "
    "with /opt/pronsoled/venv/bin/python)",
)
not_as_root = unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")


def environment(real_printrun: bool = False, **extra: str) -> dict[str, str]:
    """The environment for a pronsoled process: this checkout, plus the fake Printrun unless real."""
    ignored = ("NOTIFY_SOCKET", "PRONSOLED_CONFIG_DIR", "XDG_RUNTIME_DIR",
               "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")
    env = {key: value for key, value in os.environ.items() if key not in ignored}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT)] + ([] if real_printrun else [str(FAKES)]))
    env.update(extra)
    return env


def run_cli(*arguments: str, **options) -> subprocess.CompletedProcess:
    """Run `pronsoled ARGUMENTS` from this checkout."""
    options.setdefault("cwd", ROOT)
    options.setdefault("env", environment())
    return subprocess.run([*CLI, *arguments], capture_output=True, text=True, timeout=60, **options)


def wait_for(condition, timeout: float = 10, message: str = "the condition"):
    """Poll until condition() is true and return its value."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s waiting for {message}")
        time.sleep(0.02)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def temporary_directory(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="pronsoled-test-"))
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    return directory


@contextlib.contextmanager
def daemon_in_this_process(online_timeout: float, **env):
    """Let a test call pronsoled.daemon.serve() itself, with a short wait for the printer.

    Yields the list of Pronsole objects that the daemon code creates.
    """
    from pronsoled import daemon

    started = []
    create = daemon.Pronsole.__init__

    def create_and_record(pronsole) -> None:
        create(pronsole)
        started.append(pronsole)

    with mock.patch.dict(os.environ, environment(**env), clear=True), \
            mock.patch.object(daemon, "ONLINE_TIMEOUT", online_timeout), \
            mock.patch.object(daemon.Pronsole, "__init__", create_and_record), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            yield started
        finally:
            for pronsole in started:
                pronsole.reader.join(5)
                pronsole.process.stdin.close()
                pronsole.process.stdout.close()


class Daemon:
    """A pronsoled daemon in a subprocess, with a socket directory of its own.

    It talks to the fake Printrun in tests/fakes unless real_printrun is set.
    With explicit=False, neither the daemon nor its clients are given --config-dir.
    """

    def __init__(self, test: unittest.TestCase, port: str = "/dev/fake", *arguments: str,
                 real_printrun: bool = False, ready: bool = True, directory: Path | None = None,
                 explicit: bool = True, **env: str):
        self.test = test
        self.base = temporary_directory(test)
        self.dir = directory or self.base / "run"
        self.socket = self.dir / "pronsoled.sock"
        self.job = self.dir / "job.gcode"
        self.env = environment(real_printrun, **env)
        self.options = ["--config-dir", str(self.dir)] if explicit else []
        self.command = [*CLI, *self.options, "start", port, *arguments]
        self.process: subprocess.Popen | None = None
        self.start()
        if ready:
            self.wait_ready()

    def start(self) -> None:
        with open(self.base / "stdout", "ab") as out, open(self.base / "stderr", "ab") as err:
            self.process = subprocess.Popen(self.command, cwd=ROOT, env=self.env, stdout=out, stderr=err)
        self.test.addCleanup(self.kill)

    @property
    def stdout(self) -> str:
        return (self.base / "stdout").read_text(encoding="utf-8", errors="replace")

    @property
    def stderr(self) -> str:
        return (self.base / "stderr").read_text(encoding="utf-8", errors="replace")

    def describe(self) -> str:
        return f"\n--- daemon stdout ---\n{self.stdout}\n--- daemon stderr ---\n{self.stderr}"

    def wait_ready(self, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while "Ready: listening" not in self.stdout:
            if self.process.poll() is not None:
                self.test.fail(f"the daemon exited with code {self.process.returncode}{self.describe()}")
            if time.monotonic() > deadline:
                self.test.fail(f"the daemon was not ready after {timeout} s{self.describe()}")
            time.sleep(0.02)

    @property
    def pronsole_pid(self) -> int:
        """The fake pronsole's process ID, which it prints when it starts."""
        line = wait_for(lambda: [line for line in self.stdout.splitlines() if line.startswith("[fake] pid ")],
                        message="the fake pronsole to start")[0]
        return int(line.split()[-1])

    def cli(self, *arguments: str, **options) -> subprocess.CompletedProcess:
        """Run a client command against this daemon."""
        options.setdefault("env", self.env)
        return run_cli(*self.options, *arguments, **options)

    def ok(self, *arguments: str, **options) -> str:
        """Run a client command that must succeed, and return what it printed."""
        result = self.cli(*arguments, **options)
        self.test.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout + self.describe())
        return result.stdout

    def fails(self, *arguments: str, **options) -> str:
        """Run a client command that must fail cleanly, and return its error message."""
        result = self.cli(*arguments, **options)
        self.test.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr + self.describe())
        # One message, not a Python traceback of the client itself.
        self.test.assertTrue(result.stderr.startswith("pronsoled: error: "), result.stderr)
        return result.stderr

    def stop(self, sig: int = signal.SIGINT) -> int:
        self.process.send_signal(sig)
        return self.wait()

    def wait(self, timeout: float = 15) -> int:
        """Wait for the daemon to exit and return its exit code."""
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            self.test.fail(f"the daemon did not exit within {timeout} s{self.describe()}")

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
