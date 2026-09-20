import json
import os
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
                deadline = time.monotonic() + 5
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(socket_path.exists())

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

            self.assertFalse(socket_path.exists())
            self.assertFalse((config_dir / "pronsoled.pid").exists())


if __name__ == "__main__":
    unittest.main()
