import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


def firmware(master_fd: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if not ready:
            continue
        command = os.read(master_fd, 4096).decode(errors="replace").strip().splitlines()[0]
        if command == "eta":
            response = "Printer is not currently printing"
        elif command == "M105":
            response = "ok T:200 /200 B:50 /50"
        elif command.startswith("load "):
            response = "ok loaded"
        elif command == "print":
            response = "ok print started"
        elif command == "pause":
            response = "ok paused"
        else:
            response = "ok"
        os.write(master_fd, (response + "\n").encode())


class PtyIntegrationTest(unittest.TestCase):
    def test_daemon_uses_virtual_serial_endpoint(self):
        master_fd, slave_fd = pty.openpty()
        serial_path = os.ttyname(slave_fd)
        stop_firmware = threading.Event()
        firmware_thread = threading.Thread(
            target=firmware, args=(master_fd, stop_firmware), daemon=True
        )
        firmware_thread.start()

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
                    serial_path,
                    "115200",
                    "--pronsole",
                    str(ROOT / "tests" / "fake_serial_pronsole.py"),
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
                self.assertTrue(socket_path.exists(), process.stderr.read())

                def command(*arguments):
                    return subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pronsoled.cli",
                            "--config-dir",
                            str(config_dir),
                            *arguments,
                        ],
                        cwd=ROOT,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout

                self.assertIn("not currently printing", command("status"))
                self.assertIn("T:200", command("send", "M105"))
                setup = json.loads((config_dir / "config").read_text())
                self.assertEqual(setup["port"], serial_path)
            finally:
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=5)

        stop_firmware.set()
        os.close(master_fd)
        os.close(slave_fd)


if __name__ == "__main__":
    unittest.main()
