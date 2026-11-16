"""The real Printrun against a fake printer on a pseudo-terminal.

Skipped when Printrun is not installed for the Python that runs the tests.
After `sudo ./install.sh`, the installed copy can be used:

    /opt/pronsoled/venv/bin/python -m unittest discover -s tests

These tests also keep the fake Printrun honest: test_messages checks the exact
text that pronsoled and tests/fakes rely on.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
import unittest

from fake_firmware import FakePrinter
from support import (IDLE, ROOT, Daemon, daemon_in_this_process, environment, needs_printrun,
                     process_exists, temporary_directory, wait_for)

from pronsoled.daemon import DaemonError, request, serve


def moves(count: int) -> list[str]:
    return ["G21", "G90", "G28", *(f"G1 X{step} Y{step} F3000" for step in range(1, count + 1)), "M84"]


@needs_printrun
class RealPrintrunTest(unittest.TestCase):
    def setUp(self):
        self.home = temporary_directory(self)  # Printrun keeps its settings and log in $HOME

    def printer(self, **options) -> FakePrinter:
        printer = FakePrinter(**options)
        printer.__enter__()
        self.addCleanup(printer.__exit__)
        return printer

    def daemon(self, port: str, **options) -> Daemon:
        return Daemon(self, port, real_printrun=True, HOME=str(self.home), **options)

    def gcode(self, lines: list[str]) -> str:
        path = self.home / "part.gcode"
        path.write_text("\n".join(lines) + "\n")
        return str(path)

    def sent(self, printer: FakePrinter) -> list[str]:
        """What the printer received, without the host's own housekeeping."""
        return [command for command in printer.received if command.split()[0] not in ("M105", "M110")]

    def test_launcher_runs_pronsole_without_gui_libraries(self):
        result = subprocess.run(
            [sys.executable, "-u", str(ROOT / "pronsoled" / "_pronsole.py")], input="eta\n",
            cwd=ROOT, env=environment(real_printrun=True, HOME=str(self.home)),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(IDLE, result.stdout)
        self.assertIn("Exiting program. Goodbye!", result.stdout)
        self.assertNotIn("offline>", result.stdout)
        self.assertNotIn("\033", result.stdout)

    def test_messages(self):
        daemon = self.daemon(self.printer().port)
        self.assertIn("Printer is now online", daemon.stdout)
        self.assertEqual(daemon.ok("status"), IDLE + "\n")
        self.assertEqual(daemon.ok("abort"), "Not printing, cannot pause.\n")
        self.assertEqual(daemon.ok("send", "bogus"), "*** Unknown syntax: bogus\n")
        self.assertRegex(daemon.ok("send", "M105"), r"^SENDING:M105\n(ok T:21\.0 /0\.0 B:20\.0 /0\.0\n)+$")

        def ask(command: str) -> list[str]:
            return request(daemon.dir, command)

        job = self.gcode(moves(2))
        self.assertEqual(ask("print"), ["No file loaded. Please use load first."])
        self.assertEqual(ask("load /no/such/file.gcode"), ["Loading file: /no/such/file.gcode", "File not found!"])
        self.assertEqual(ask(f"load {job}")[:2], [f"Loading file: {job}", f"Loaded {job}, 6 lines."])
        ask("disconnect")
        self.assertEqual(ask("print"), ["Not connected to printer."])
        self.assertEqual(ask("M105"), ["Printer is not online."])

    def test_print_reaches_the_printer_complete_and_in_order(self):
        printer = self.printer(move_seconds=0.1)
        daemon = self.daemon(printer.port)
        job = self.gcode(moves(30))
        self.assertEqual(daemon.ok("print", job), f"Print job started: {job}\n")
        self.assertIn(f"Printing {daemon.job}", daemon.stdout)

        wait_for(lambda: "G1 X3 Y3 F3000" in printer.received, message="the print to start")
        self.assertRegex(daemon.ok("status"), r"^Est: .* remaining\n$")
        self.assertIn("the printer is busy: Est: ", daemon.fails("print", job))

        wait_for(lambda: "M84" in printer.received, timeout=30, message="the print to finish")
        self.assertEqual(self.sent(printer), moves(30))
        wait_for(lambda: "Print ended at" in daemon.stdout, message="pronsole to report the end of the print")
        self.assertEqual(daemon.ok("status"), IDLE + "\n")

    def test_abort_stops_the_print(self):
        printer = self.printer(move_seconds=0.1)
        daemon = self.daemon(printer.port)
        daemon.ok("print", self.gcode(moves(200)))
        wait_for(lambda: "G1 X3 Y3 F3000" in printer.received, message="the print to start")
        self.assertEqual(daemon.ok("abort"), "Print paused\n")
        time.sleep(1)
        received = len(self.sent(printer))
        time.sleep(1)
        self.assertEqual(len(self.sent(printer)), received, "the printer still receives moves after abort")
        self.assertLess(received, 100)
        self.assertEqual(daemon.ok("status"), IDLE + "\n")

    def test_large_file_starts_printing(self):
        printer = self.printer()
        daemon = self.daemon(printer.port)
        job = self.gcode(moves(100_000))
        self.assertEqual(daemon.ok("print", job), f"Print job started: {job}\n")
        wait_for(lambda: "G1 X50 Y50 F3000" in printer.received, timeout=60, message="the print to start")

    def test_port_that_does_not_exist(self):
        started = time.monotonic()
        daemon = self.daemon("/dev/pronsoled-test-no-such-port", ready=False)
        self.assertEqual(daemon.wait(), 1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(daemon.stderr, "pronsoled: error: Connection error: Could not connect to serial port "
                                        "'/dev/pronsoled-test-no-such-port'\n")

    def test_printer_that_never_answers(self):
        printer = self.printer(answers=False)
        directory = temporary_directory(self)
        with daemon_in_this_process(online_timeout=3, real_printrun=True, HOME=str(self.home)):
            with self.assertRaisesRegex(DaemonError, "did not come online within 3 seconds"):
                serve(directory, printer.port, 115200)
        self.assertEqual(list(directory.iterdir()), [])

    def test_unplugged_printer_ends_the_daemon(self):
        """pronsole would carry on with a dead connection and report prints as started."""
        with FakePrinter() as printer:
            daemon = self.daemon(printer.port)
        self.assertEqual(daemon.wait(), 1)
        self.assertIn("pronsoled: error: lost the printer: Can't read from printer (disconnected?)", daemon.stderr)
        self.assertFalse(daemon.socket.exists())

    def test_file_that_is_not_text_ends_pronsole_and_the_daemon(self):
        """pronsole raises on such a file. Left alone, its status thread would keep it half alive."""
        daemon = self.daemon(self.printer().port)
        job = self.home / "binary.gcode"
        job.write_bytes(b"\xff\xfe\x00\x01 not text\n")
        daemon.fails("print", str(job))
        self.assertEqual(daemon.wait(), 1)
        self.assertIn("UnicodeDecodeError", daemon.stdout)
        self.assertIn("pronsole exited unexpectedly with code 1", daemon.stderr)

    def test_stopping_the_daemon_stops_pronsole(self):
        daemon = self.daemon(self.printer().port)
        children = subprocess.run(["ps", "-o", "pid=", "--ppid", str(daemon.process.pid)],
                                  capture_output=True, text=True).stdout.split()
        self.assertEqual(len(children), 1, "the daemon should have exactly one child: pronsole")
        self.assertEqual(daemon.stop(signal.SIGTERM), 0)
        wait_for(lambda: not process_exists(int(children[0])), message="pronsole to exit")

    def test_killed_daemon_takes_pronsole_with_it(self):
        daemon = self.daemon(self.printer().port)
        child = int(subprocess.run(["ps", "-o", "pid=", "--ppid", str(daemon.process.pid)],
                                   capture_output=True, text=True).stdout.split()[0])
        daemon.kill()
        wait_for(lambda: not process_exists(child), message="pronsole to exit")

    def test_printrun_keeps_its_own_log_and_settings_in_the_home_directory(self):
        """The README says so under "Logs"."""
        daemon = self.daemon(self.printer().port)
        daemon.ok("status")
        self.assertIn("Printer is now online", (self.home / "printrun.log").read_text())
        self.assertTrue((self.home / ".config" / "Printrun" / "pronsolerc").is_file())

    def test_replies_and_log_contain_no_prompt_or_control_characters(self):
        daemon = self.daemon(self.printer().port)
        replies = [daemon.ok("status"), daemon.ok("send", "M105"), daemon.ok("send", "M114"), daemon.ok("send", "help")]
        for text in ("".join(replies), daemon.stdout):
            self.assertNotRegex(text, r"[\x00-\x08\x0b-\x1f]|offline>|PC>")


if __name__ == "__main__":
    unittest.main()
