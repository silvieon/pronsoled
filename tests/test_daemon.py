"""The daemon: starting, stopping, failing, telling systemd, and the socket protocol."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import threading
import time
import unittest

from support import (IDLE, Daemon, daemon_in_this_process, not_as_root, process_exists,
                     temporary_directory, wait_for)

from pronsoled.daemon import DaemonError, request, serve


def can_connect(path) -> bool:
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(str(path))
        except OSError:
            return False
        return True


class LifecycleTest(unittest.TestCase):
    def test_ready_after_the_printer_is_online(self):
        daemon = Daemon(self)
        output = daemon.stdout
        self.assertIn("Starting pronsoled for /dev/fake @ 115200", output)
        self.assertLess(output.index("Printer is now online"), output.index("Ready: listening on"))
        self.assertIn(f"Ready: listening on {daemon.socket}", output)
        self.assertEqual(daemon.stderr, "")

    def test_socket_is_for_owner_and_group_only(self):
        daemon = Daemon(self)
        mode = daemon.socket.stat().st_mode
        self.assertTrue(stat.S_ISSOCK(mode))
        self.assertEqual(stat.S_IMODE(mode), 0o660)

    def test_port_and_baud_rate_reach_pronsole(self):
        daemon = Daemon(self, "/dev/ttyUSB7", "250000")
        self.assertIn("Starting pronsoled for /dev/ttyUSB7 @ 250000", daemon.stdout)
        self.assertIn("[fake] connect /dev/ttyUSB7 250000", daemon.stdout)

    def test_creates_the_socket_directory(self):
        base = temporary_directory(self)
        daemon = Daemon(self, directory=base / "a" / "b" / "c")
        self.assertIn(IDLE, daemon.ok("status"))

    def test_ctrl_c_stops_cleanly(self):
        daemon = Daemon(self)
        pronsole = daemon.pronsole_pid
        self.assertEqual(daemon.stop(signal.SIGINT), 0)
        self.assertFalse(daemon.socket.exists())
        self.assertIn("Stopped", daemon.stdout)
        self.assertEqual(daemon.stderr, "")
        wait_for(lambda: not process_exists(pronsole), message="pronsole to exit")

    def test_sigterm_stops_cleanly(self):
        """What `systemctl stop` sends. Exit code 0 keeps the unit from being marked failed."""
        daemon = Daemon(self)
        pronsole = daemon.pronsole_pid
        self.assertEqual(daemon.stop(signal.SIGTERM), 0)
        self.assertFalse(daemon.socket.exists())
        self.assertEqual(daemon.stderr, "")
        wait_for(lambda: not process_exists(pronsole), message="pronsole to exit")

    def test_sigterm_while_waiting_for_the_printer(self):
        daemon = Daemon(self, "/dev/silent", ready=False)
        pronsole = daemon.pronsole_pid
        self.assertEqual(daemon.stop(signal.SIGTERM), 0)
        self.assertFalse(daemon.socket.exists())
        wait_for(lambda: not process_exists(pronsole), message="pronsole to exit")

    def test_restart_in_the_same_directory(self):
        daemon = Daemon(self)
        self.assertEqual(daemon.stop(), 0)
        again = Daemon(self, directory=daemon.dir)
        self.assertIn(IDLE, again.ok("status"))

    def test_killed_daemon_takes_pronsole_with_it_and_can_be_replaced(self):
        daemon = Daemon(self)
        pronsole = daemon.pronsole_pid
        daemon.kill()
        wait_for(lambda: not process_exists(pronsole), message="pronsole to exit")
        self.assertTrue(daemon.socket.exists(), "a killed daemon cannot remove its socket")
        self.assertIn("daemon is not running", daemon.fails("status"))

        replacement = Daemon(self, directory=daemon.dir)
        self.assertIn(IDLE, replacement.ok("status"))

    def test_second_daemon_on_the_same_socket_refuses_to_start(self):
        """Two pronsoles on one serial port is the problem pronsoled exists to prevent."""
        first = Daemon(self)
        second = Daemon(self, directory=first.dir, ready=False)
        self.assertEqual(second.wait(), 1)
        self.assertIn(f"pronsoled: error: a daemon is already running on {first.socket}", second.stderr)
        self.assertNotIn("[fake] pid", second.stdout, "the second daemon must not start pronsole")
        self.assertIn(IDLE, first.ok("status"))


class StartupFailureTest(unittest.TestCase):
    def test_port_that_cannot_be_opened(self):
        started = time.monotonic()
        daemon = Daemon(self, "/dev/missing", ready=False)
        self.assertEqual(daemon.wait(), 1)
        self.assertLess(time.monotonic() - started, 10, "must not wait for the online timeout")
        self.assertEqual(
            daemon.stderr,
            "pronsoled: error: Connection error: Could not connect to serial port '/dev/missing'\n",
        )
        self.assertNotIn("Ready", daemon.stdout)
        self.assertFalse(daemon.socket.exists())

    def test_pronsole_dies_while_connecting(self):
        daemon = Daemon(self, "/dev/crash", ready=False)
        self.assertEqual(daemon.wait(), 1)
        self.assertEqual(daemon.stderr, "pronsoled: error: pronsole exited unexpectedly with code 3\n")
        self.assertIn("fake pronsole crashing", daemon.stdout)
        self.assertFalse(daemon.socket.exists())

    def test_no_socket_until_the_printer_answers(self):
        daemon = Daemon(self, "/dev/silent", ready=False)
        daemon.pronsole_pid  # pronsole is running and has been told to connect
        time.sleep(1.5)
        self.assertIsNone(daemon.process.poll())
        self.assertFalse(daemon.socket.exists())
        self.assertNotIn("Ready", daemon.stdout)

    def test_printer_that_never_answers_fails_after_the_timeout(self):
        directory = temporary_directory(self)
        with daemon_in_this_process(online_timeout=1) as started:
            with self.assertRaisesRegex(DaemonError, "/dev/silent did not come online within 1 seconds"):
                serve(directory, "/dev/silent", 115200)
            self.assertIsNotNone(started[0].process.poll(), "pronsole must be stopped when startup fails")
        self.assertEqual(list(directory.iterdir()), [])

    @not_as_root
    def test_socket_directory_that_cannot_be_created(self):
        base = temporary_directory(self)
        base.chmod(0o500)
        self.addCleanup(base.chmod, 0o700)
        daemon = Daemon(self, directory=base / "run", ready=False)
        self.assertEqual(daemon.wait(), 1)
        self.assertTrue(daemon.stderr.startswith("pronsoled: error: [Errno 13] Permission denied"), daemon.stderr)
        self.assertNotIn("[fake] pid", daemon.stdout, "pronsole must not be started")


class SystemdNotifyTest(unittest.TestCase):
    """Type=notify: `systemctl start` returns when the daemon sends READY=1."""

    def notify_socket(self, address: str) -> socket.socket:
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(address)
        receiver.settimeout(15)
        return receiver

    def test_ready_is_sent_once_clients_can_connect(self):
        address = str(temporary_directory(self) / "notify")
        receiver = self.notify_socket(address)
        daemon = Daemon(self, ready=False, NOTIFY_SOCKET=address)
        self.assertEqual(receiver.recv(100), b"READY=1")
        self.assertTrue(can_connect(daemon.socket), "READY=1 arrived before the socket was listening")

    def test_abstract_notify_socket(self):
        name = f"pronsoled-test-{os.getpid()}"
        receiver = self.notify_socket("\0" + name)
        Daemon(self, ready=False, NOTIFY_SOCKET="@" + name)
        self.assertEqual(receiver.recv(100), b"READY=1")

    def test_nothing_is_sent_when_startup_fails(self):
        address = str(temporary_directory(self) / "notify")
        receiver = self.notify_socket(address)
        daemon = Daemon(self, "/dev/missing", ready=False, NOTIFY_SOCKET=address)
        self.assertEqual(daemon.wait(), 1)
        receiver.settimeout(0.2)
        with self.assertRaises(socket.timeout):
            receiver.recv(100)


class PronsoleDeathTest(unittest.TestCase):
    def test_daemon_exits_so_that_systemd_restarts_it(self):
        daemon = Daemon(self)
        self.assertEqual(daemon.ok("send", "die"), "fake pronsole crashing\n")
        self.assertEqual(daemon.wait(), 1)
        self.assertEqual(daemon.stderr, "pronsoled: error: pronsole exited unexpectedly with code 3\n")
        self.assertIn("fake pronsole crashing", daemon.stdout)
        self.assertFalse(daemon.socket.exists())

    def test_clients_are_told(self):
        daemon = Daemon(self)
        daemon.ok("send", "die")
        self.assertRegex(daemon.fails("status"), "is not running|closed the connection without answering")

    def test_daemon_exits_when_the_printer_goes_away(self):
        """pronsole carries on without a printer and never reconnects: only a restart helps."""
        daemon = Daemon(self)
        daemon.cli("send", "unplug")
        self.assertEqual(daemon.wait(), 1)
        self.assertEqual(daemon.stderr, "pronsoled: error: lost the printer: Can't read from printer "
                                        "(disconnected?) Unable to read from serial port '/dev/fake'\n")
        self.assertFalse(daemon.socket.exists())

    def test_failing_command_ends_pronsole_instead_of_leaving_it_hung(self):
        """pronsole exits when a command raises. Its status thread must not keep it half alive."""
        daemon = Daemon(self)
        garbage = daemon.base / "garbage.gcode"
        garbage.write_bytes(b"\xff\xfe\x00 not text\n")
        daemon.cli("send", f"load {garbage}")
        self.assertEqual(daemon.wait(), 1)
        self.assertIn("UnicodeDecodeError", daemon.stdout)
        self.assertIn("pronsole exited unexpectedly with code 1", daemon.stderr)


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.daemon = Daemon(self)

    def raw(self, payload: bytes) -> dict:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(str(self.daemon.socket))
            connection.sendall(payload)
            return json.loads(connection.makefile().readline())

    def ask(self, command: str) -> list[str]:
        return request(self.daemon.dir, command)

    def test_request_and_reply(self):
        self.assertEqual(self.raw(b'{"command": "eta"}\n'), {"ok": True, "output": [IDLE]})

    def test_extra_fields_are_ignored(self):
        self.assertEqual(self.raw(b'{"command": "eta", "id": 7}\n'), {"ok": True, "output": [IDLE]})

    def test_malformed_requests_are_refused_and_the_daemon_survives(self):
        for payload in (b"not json\n", b"{}\n", b"[]\n", b'"eta"\n', b'{"command": 5}\n',
                        b'{"command": null}\n', b'{"command": ["eta"]}\n', b"\n", b"\xff\xfe\n"):
            with self.subTest(payload=payload):
                reply = self.raw(payload)
                self.assertFalse(reply["ok"])
                self.assertIn("bad request", reply["error"])
        self.assertEqual(self.ask("eta"), [IDLE])

    def test_clients_that_hang_up_do_not_disturb_the_daemon(self):
        for payload in (b"", b'{"command": "eta"}\n', b'{"command": "lines 50"}\n'):
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(self.daemon.socket))
                connection.sendall(payload)
        self.assertEqual(self.ask("eta"), [IDLE])
        self.assertIsNone(self.daemon.process.poll())

    def test_printer_answer_follows_the_command(self):
        self.assertEqual(self.ask("M105"), ["SENDING:M105", "ok T:21.0 /0.0 B:20.0 /0.0"])

    def test_long_reply_arrives_whole_and_in_order(self):
        self.assertEqual(self.ask("lines 40"), [f"line {number}" for number in range(40)])

    def test_reply_to_a_command_that_prints_nothing_is_empty(self):
        self.assertEqual(self.ask("G28"), ["SENDING:G28"])
        self.ask("load /dev/null")
        self.ask("print")
        self.assertEqual(self.ask("pause"), [])

    def test_output_from_before_a_command_is_not_part_of_its_reply(self):
        self.assertEqual(self.ask("later 1.4 thermal runaway"), [])
        wait_for(lambda: "thermal runaway" in self.daemon.stdout, message="the late message to be logged")
        self.assertEqual(self.ask("eta"), [IDLE])

    def test_slow_command_delays_but_does_not_lose_the_next_one(self):
        self.assertEqual(self.ask("sleep 1.5"), [])
        self.assertIn(IDLE, self.ask("eta"))
        self.assertEqual(self.ask("eta"), [IDLE])

    def test_endless_output_cannot_hold_a_reply_forever(self):
        started = time.monotonic()
        reply = self.ask("lines 400")  # 8 seconds of output
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(reply, [f"line {number}" for number in range(len(reply))])
        self.assertLess(len(reply), 400)

    def test_replies_and_log_contain_no_prompt_or_control_characters(self):
        replies = [line for command in ("eta", "M105", "bogus", "lines 3") for line in self.ask(command)]
        for text in ("\n".join(replies), self.daemon.stdout):
            self.assertNotRegex(text, r"[\x00-\x08\x0b-\x1f]|offline>|fake 21")
        self.assertNotIn("\n\n", self.daemon.stdout)

    def test_non_ascii_text_survives(self):
        self.assertEqual(self.ask("prüfe düse — 温度"), ["*** Unknown syntax: prüfe düse — 温度"])

    def test_simultaneous_clients_each_get_their_own_reply(self):
        replies: dict[int, list[str]] = {}

        def client(number: int) -> None:
            replies[number] = self.ask(f"ping-{number}")

        threads = [threading.Thread(target=client, args=(number,)) for number in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(replies, {number: [f"*** Unknown syntax: ping-{number}"] for number in range(12)})

    def test_everything_pronsole_prints_is_logged(self):
        self.ask("M105")
        self.ask("bogus")
        log = self.daemon.stdout
        for line in ("WARNING:root:Memory-efficient GCoder implementation unavailable",
                     "Welcome to the printer console!", "Printer is now online",
                     "SENDING:M105", "ok T:21.0 /0.0 B:20.0 /0.0", "*** Unknown syntax: bogus"):
            self.assertIn(line, log)


if __name__ == "__main__":
    unittest.main()
