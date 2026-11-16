"""The command line: client commands, their errors, arguments, and where the socket is looked for."""
from __future__ import annotations

import os
import socket
import stat
import threading
import unittest
from pathlib import Path
from unittest import mock

from support import Daemon, not_as_root, run_cli, temporary_directory

import pronsoled
from pronsoled import cli
from pronsoled.daemon import DaemonError

IDLE = "Printer is not currently printing. No ETA available.\n"
PRINTING = "Est: 0:00:10 of 0:01:00 remaining\n"
SYSTEM_SERVICE_PRESENT = Path("/run/pronsoled").exists()


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.daemon = Daemon(self)
        self.home = self.daemon.base / "home"
        self.home.mkdir()

    def gcode(self, name: str = "part.gcode", text: str = "G28\nG1 X10 Y10\nM84\n") -> Path:
        path = self.home / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_status(self):
        self.assertEqual(self.daemon.ok("status"), IDLE)

    def test_send_gcode(self):
        self.assertEqual(self.daemon.ok("send", "M105"), "SENDING:M105\nok T:21.0 /0.0 B:20.0 /0.0\n")

    def test_send_pronsole_command_with_arguments(self):
        self.assertEqual(self.daemon.ok("send", "lines 3"), "line 0\nline 1\nline 2\n")

    def test_send_unknown_command_shows_what_pronsole_said(self):
        self.assertEqual(self.daemon.ok("send", "bogus"), "*** Unknown syntax: bogus\n")

    def test_print(self):
        job = self.gcode()
        self.assertEqual(self.daemon.ok("print", str(job)), f"Print job started: {job}\n")
        self.assertEqual(self.daemon.job.read_text(), job.read_text())
        self.assertIn(f"Loaded {self.daemon.job}, 3 lines.", self.daemon.stdout)
        self.assertIn(f"Printing {self.daemon.job}", self.daemon.stdout)
        self.assertEqual(self.daemon.ok("status"), PRINTING)

    def test_print_is_refused_while_printing(self):
        self.daemon.ok("print", str(self.gcode()))
        other = self.gcode("other.gcode", "G1 Z99\n")
        self.assertEqual(self.daemon.fails("print", str(other)),
                         f"pronsoled: error: the printer is busy: {PRINTING}")
        self.assertNotEqual(self.daemon.job.read_text(), other.read_text(), "the running job must stay in place")

    def test_abort_pauses_the_print(self):
        self.daemon.ok("print", str(self.gcode()))
        self.assertEqual(self.daemon.ok("abort"), "Print paused\n")
        self.assertEqual(self.daemon.ok("status"), IDLE)

    def test_abort_without_a_print_shows_what_pronsole_said(self):
        self.assertEqual(self.daemon.ok("abort"), "Not printing, cannot pause.\n")

    def test_print_replaces_the_previous_job(self):
        self.daemon.ok("print", str(self.gcode("first.gcode", "G1 X1\n")))
        self.daemon.ok("abort")
        self.daemon.job.chmod(0o400)
        self.daemon.ok("print", str(self.gcode("second.gcode", "G1 X2\nG1 X3\n")))
        self.assertEqual(self.daemon.job.read_text(), "G1 X2\nG1 X3\n")
        self.assertIn(f"Loaded {self.daemon.job}, 2 lines.", self.daemon.stdout)

    def test_print_file_name_with_spaces_and_non_ascii_characters(self):
        job = self.gcode("my part (v2) düse.gcode")
        self.assertEqual(self.daemon.ok("print", str(job)), f"Print job started: {job}\n")

    def test_print_relative_path(self):
        self.gcode("relative.gcode")
        self.assertEqual(self.daemon.ok("print", "relative.gcode", cwd=self.home),
                         "Print job started: relative.gcode\n")

    def test_staged_job_is_readable_by_the_daemons_group_whatever_the_umask(self):
        """The service runs as another user: it reads the job through its group."""
        job = self.gcode()
        self.home.chmod(0o700)  # a private home directory, which the service could not enter
        self.daemon.ok("print", str(job), preexec_fn=lambda: os.umask(0o077))
        self.assertEqual(stat.S_IMODE(self.daemon.job.stat().st_mode), 0o640)

    def test_print_missing_file(self):
        missing = self.home / "missing.gcode"
        self.assertEqual(self.daemon.fails("print", str(missing)),
                         f"pronsoled: error: G-code file does not exist: {missing}\n")
        self.assertNotIn("Loading file", self.daemon.stdout)

    def test_print_directory(self):
        self.assertIn("G-code file does not exist", self.daemon.fails("print", str(self.home)))

    @not_as_root
    def test_print_unreadable_file(self):
        job = self.gcode()
        job.chmod(0)
        self.assertIn("Permission denied", self.daemon.fails("print", str(job)))
        self.assertNotIn("Loading file", self.daemon.stdout)

    def test_print_while_the_printer_is_offline(self):
        self.daemon.ok("send", "disconnect")
        self.assertEqual(self.daemon.fails("print", str(self.gcode())),
                         "pronsoled: error: `print` failed: Not connected to printer.\n")

    def test_print_file_that_is_not_text(self):
        """pronsole cannot load it and exits: the client must say so, not report a started print."""
        job = self.home / "binary.gcode"
        job.write_bytes(b"\xff\xfe\x00\x01 not text\n")
        self.daemon.fails("print", str(job))
        self.assertEqual(self.daemon.wait(), 1)


class WithoutDaemonTest(unittest.TestCase):
    def assert_not_running(self, directory: Path, *arguments: str):
        result = run_cli("--config-dir", str(directory), *arguments)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(
            result.stderr,
            f"pronsoled: error: daemon is not running: no socket at {directory}/pronsoled.sock. "
            "Start it with `sudo systemctl start pronsoled` (if that fails, see "
            "`journalctl -u pronsoled`) or by hand with `pronsoled start`.\n",
        )

    def test_every_client_command_says_the_daemon_is_not_running(self):
        directory = temporary_directory(self)
        job = directory / "part.gcode"
        job.write_text("G28\n")
        for arguments in (["status"], ["send", "M105"], ["abort"], ["print", str(job)]):
            with self.subTest(arguments=arguments):
                self.assert_not_running(directory, *arguments)
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ["part.gcode"])

    def test_socket_directory_that_does_not_exist(self):
        self.assert_not_running(temporary_directory(self) / "nowhere", "status")

    def test_socket_left_behind_by_a_killed_daemon(self):
        directory = temporary_directory(self)
        with socket.socket(socket.AF_UNIX) as stale:
            stale.bind(str(directory / "pronsoled.sock"))
        self.assert_not_running(directory, "status")

    @not_as_root
    def test_user_outside_the_group_is_told_how_to_get_in(self):
        daemon = Daemon(self)
        daemon.socket.chmod(0)
        self.assertEqual(
            daemon.fails("status"),
            f"pronsoled: error: permission denied on {daemon.socket}. Add yourself to the pronsoled group "
            "(`sudo usermod -aG pronsoled $USER`), then log out and back in.\n",
        )

    def answer_once(self, reply: bytes) -> Path:
        """A stand-in daemon that answers one request with `reply`."""
        directory = temporary_directory(self)
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(str(directory / "pronsoled.sock"))
        server.listen()

        def serve() -> None:
            connection, _ = server.accept()
            with connection:
                connection.makefile().readline()
                connection.sendall(reply)

        threading.Thread(target=serve, daemon=True).start()
        return directory

    def test_error_from_the_daemon_is_shown(self):
        directory = self.answer_once(b'{"ok": false, "error": "pronsole is not running"}\n')
        result = run_cli("--config-dir", str(directory), "status")
        self.assertEqual((result.returncode, result.stderr), (1, "pronsoled: error: pronsole is not running\n"))

    def test_daemon_that_goes_away_without_answering(self):
        directory = self.answer_once(b"")
        result = run_cli("--config-dir", str(directory), "status")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, f"pronsoled: error: the daemon at {directory}/pronsoled.sock "
                                        "closed the connection without answering\n")


class ArgumentTest(unittest.TestCase):
    def test_version(self):
        result = run_cli("--version")
        self.assertEqual((result.returncode, result.stdout), (0, f"pronsoled {pronsoled.__version__}\n"))

    def test_help_lists_every_command(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for command in ("start", "status", "send", "print", "abort", "--config-dir", "--version"):
            self.assertIn(command, result.stdout)

    def test_usage_errors(self):
        for arguments in ([], ["bogus"], ["send"], ["print"], ["start", "/dev/fake", "fast"],
                          ["status", "extra"], ["send", "M105", "M114"]):
            with self.subTest(arguments=arguments):
                result = run_cli(*arguments)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("usage: pronsoled", result.stderr)

    def test_options_removed_in_0_2_0_are_rejected_clearly(self):
        """An old systemd override that still passes them must fail with a readable message."""
        for option in ("--log-file", "--pronsole"):
            with self.subTest(option=option):
                result = run_cli("start", "/dev/fake", "115200", option, "/tmp/x")
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"unrecognized arguments: {option}", result.stderr)

    def test_start_defaults(self):
        arguments = cli.parser().parse_args(["start"])
        self.assertEqual((arguments.port, arguments.baud), ("auto", 115200))


class PortDetectionTest(unittest.TestCase):
    def setUp(self):
        self.dev = temporary_directory(self)

    def add(self, *names: str) -> None:
        for name in names:
            (self.dev / name).touch()

    def test_the_only_acm_port_is_used(self):
        self.add("ttyACM0", "ttyUSB0", "ttyS0", "tty")
        self.assertEqual(cli.find_port(self.dev), f"{self.dev}/ttyACM0")

    def test_no_port(self):
        self.add("ttyUSB0", "ttyS0")
        with self.assertRaisesRegex(DaemonError, "found none; name the port explicitly"):
            cli.find_port(self.dev)

    def test_several_ports(self):
        self.add("ttyACM1", "ttyACM0")
        with self.assertRaisesRegex(DaemonError, f"found {self.dev}/ttyACM0, {self.dev}/ttyACM1; name the port"):
            cli.find_port(self.dev)

    @unittest.skipIf(list(Path("/dev").glob("ttyACM*")), "this machine has a /dev/ttyACM* port")
    def test_start_without_a_printer_fails_before_starting_pronsole(self):
        directory = temporary_directory(self)
        result = run_cli("--config-dir", str(directory), "start")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "pronsoled: error: expected exactly one /dev/ttyACM* port but found none; "
                                        "name the port explicitly\n")
        self.assertNotIn("[fake] pid", result.stdout)


class SocketDirectoryTest(unittest.TestCase):
    """Which directory is used when --config-dir is not given."""

    def setUp(self):
        base = temporary_directory(self)
        self.system = base / "run-pronsoled"
        self.runtime = base / "xdg"
        self.home = base / "home"
        patches = (
            mock.patch.object(cli, "SYSTEM_DIR", self.system),
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime), "HOME": str(self.home)}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def user_socket(self) -> None:
        (self.runtime / "pronsoled").mkdir(parents=True)
        (self.runtime / "pronsoled" / "pronsoled.sock").touch()

    def test_hand_started_daemon_uses_the_users_runtime_directory(self):
        self.system.mkdir()
        self.assertEqual(cli.socket_dir(daemon=True), self.runtime / "pronsoled")

    def test_hand_started_daemon_without_a_runtime_directory_uses_the_home_directory(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(cli.socket_dir(daemon=True), self.home / ".local" / "state" / "pronsoled")

    def test_client_uses_the_service_while_it_runs(self):
        self.system.mkdir()
        self.user_socket()
        self.assertEqual(cli.socket_dir(daemon=False), self.system)

    def test_client_uses_a_hand_started_daemon_when_the_service_is_stopped(self):
        self.user_socket()
        self.assertEqual(cli.socket_dir(daemon=False), self.runtime / "pronsoled")

    def test_client_names_the_service_when_nothing_runs(self):
        self.assertEqual(cli.socket_dir(daemon=False), self.system)

    def test_environment_variable_and_flag(self):
        self.assertIsNone(cli.parser().parse_args(["status"]).config_dir)
        with mock.patch.dict(os.environ, {"PRONSOLED_CONFIG_DIR": "/from/env"}):
            self.assertEqual(cli.parser().parse_args(["status"]).config_dir, Path("/from/env"))
            self.assertEqual(cli.parser().parse_args(["--config-dir", "/flag", "status"]).config_dir, Path("/flag"))
        with mock.patch.dict(os.environ, {"PRONSOLED_CONFIG_DIR": ""}):
            self.assertIsNone(cli.parser().parse_args(["status"]).config_dir)


@unittest.skipIf(SYSTEM_SERVICE_PRESENT, "the pronsoled service is running here: clients would use it")
class DefaultDirectoryEndToEndTest(unittest.TestCase):
    """A daemon and a client that are told nothing must still find each other."""

    def test_in_the_runtime_directory(self):
        runtime = temporary_directory(self)
        daemon = Daemon(self, directory=runtime / "pronsoled", explicit=False, XDG_RUNTIME_DIR=str(runtime))
        self.assertTrue(daemon.socket.exists())
        self.assertEqual(daemon.ok("status"), IDLE)

    def test_in_the_home_directory(self):
        home = temporary_directory(self)
        daemon = Daemon(self, directory=home / ".local" / "state" / "pronsoled", explicit=False, HOME=str(home))
        self.assertTrue(daemon.socket.exists())
        self.assertEqual(daemon.ok("status"), IDLE)

    def test_where_the_environment_variable_says(self):
        directory = temporary_directory(self) / "custom"
        daemon = Daemon(self, directory=directory, explicit=False, PRONSOLED_CONFIG_DIR=str(directory))
        self.assertTrue(daemon.socket.exists())
        self.assertEqual(daemon.ok("status"), IDLE)

    def test_client_names_the_service_when_nothing_runs(self):
        result = run_cli("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no socket at /run/pronsoled/pronsoled.sock", result.stderr)


if __name__ == "__main__":
    unittest.main()
