"""pronsoled/_pronsole.py, which starts pronsole without GUI libraries and without a prompt."""
from __future__ import annotations

import subprocess
import sys
import unittest

from support import ROOT, environment

LAUNCHER = [sys.executable, "-u", str(ROOT / "pronsoled" / "_pronsole.py")]


def launch(commands: str) -> subprocess.CompletedProcess:
    return subprocess.run(LAUNCHER, input=commands, cwd=ROOT, env=environment(),
                          capture_output=True, text=True, timeout=30)


class LauncherTest(unittest.TestCase):
    def test_pronsole_starts_although_wxpython_is_missing(self):
        """The fake printrun imports wx and calls printrun.gui.widgets.get_space, like the real one."""
        result = launch("")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertIn("Welcome to the printer console!", result.stdout)
        self.assertIn("Exiting program. Goodbye!", result.stdout)

    def test_no_prompt_is_printed(self):
        result = launch("eta\nconnect /dev/fake 115200\neta\n")
        self.assertEqual(result.stdout.count("Printer is not currently printing"), 2)
        self.assertNotIn("offline>", result.stdout)
        self.assertNotIn("fake 21", result.stdout)
        self.assertNotIn("\033", result.stdout)

    def test_end_of_input_ends_pronsole(self):
        """This is how pronsole goes away if the daemon is killed."""
        result = launch("connect /dev/fake 115200\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("[fake] disconnected", result.stdout)

    def test_failing_command_ends_the_process(self):
        """Without interp.kill(), pronsole's status thread keeps the process alive forever."""
        result = launch(f"connect /dev/fake 115200\nload {sys.executable}\neta\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("UnicodeDecodeError", result.stderr)
        self.assertIn("[fake] disconnected", result.stdout)
        self.assertIn("[fake] status thread stopped", result.stdout)

    def test_importing_the_launcher_does_nothing(self):
        result = subprocess.run(
            [sys.executable, "-c", "import sys, pronsoled._pronsole; print('wx' in sys.modules, 'printrun' in sys.modules)"],
            cwd=ROOT, env=environment(), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual((result.returncode, result.stdout), (0, "False False\n"))


if __name__ == "__main__":
    unittest.main()
