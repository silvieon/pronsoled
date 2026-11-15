import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


class PythonCliTest(unittest.TestCase):
    def test_command_surface_and_setup_record(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_dir = Path(temporary_dir) / "config"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "pronsoled.cli",
                    "--config-dir",
                    str(config_dir),
                    "start",
                    "/dev/fake-printer",
                    "115200",
                    "--pronsole",
                    str(ROOT / "tests" / "fake_pronsole.py"),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                socket_path = config_dir / "pronsoled.sock"
                deadline = time.monotonic() + 20  # startup self-test takes ~5s
                while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not socket_path.exists():
                    process.kill()
                    self.fail(process.communicate()[1])
                self.assertEqual(socket_path.stat().st_mode & 0o777, 0o660)

                def command(*arguments):
                    return subprocess.run(
                        [sys.executable, "-m", "pronsoled.cli", "--config-dir", str(config_dir), *arguments],
                        cwd=ROOT,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout

                self.assertIn("not currently printing", command("status"))
                self.assertIn("echo: M105", command("send", "M105"))
                gcode = Path(temporary_dir) / "job.gcode"
                gcode.write_text("G28\n", encoding="utf-8")
                self.assertIn("Print job started", command("print", str(gcode)))
                self.assertIn("Print paused", command("abort"))

                setup = json.loads((config_dir / "config").read_text(encoding="utf-8"))
                self.assertEqual(setup["port"], "/dev/fake-printer")
                self.assertEqual(setup["baud"], 115200)
                self.assertEqual(setup["socket"], str(socket_path))
            finally:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=5)

            self.assertEqual(process.returncode, 0)  # clean stop, so systemd does not mark it failed
            self.assertFalse(socket_path.exists())
            self.assertFalse((config_dir / "pronsoled.pid").exists())

    def test_daemon_exits_when_pronsole_dies(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_dir = Path(temporary_dir) / "config"
            process = subprocess.Popen(
                [
                    sys.executable, "-m", "pronsoled.cli", "--config-dir", str(config_dir),
                    "start", "/dev/fake-printer", "115200",
                    "--pronsole", str(ROOT / "tests" / "fake_pronsole.py"),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            socket_path = config_dir / "pronsoled.sock"
            deadline = time.monotonic() + 20
            while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not socket_path.exists():
                process.kill()
                self.fail(process.communicate()[1])

            subprocess.run(
                [sys.executable, "-m", "pronsoled.cli", "--config-dir", str(config_dir), "send", "die"],
                cwd=ROOT, capture_output=True, text=True,
            )
            try:
                _, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                self.fail("daemon kept running after pronsole died")

            self.assertEqual(process.returncode, 1)  # non-zero, so systemd restarts it
            self.assertIn("exited unexpectedly with code 3", stderr)
            self.assertIn("fake pronsole crashing", stderr)
            self.assertFalse(socket_path.exists())


if __name__ == "__main__":
    unittest.main()
