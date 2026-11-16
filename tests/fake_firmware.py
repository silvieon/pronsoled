#!/usr/bin/env python3
"""A fake Marlin-style printer on a pseudo-terminal, for testing without hardware.

As a program (run as root, so the port can be handed to the serial group):

    fake_firmware.py /dev/ttyFAKE0

creates the port as a symlink to a pty and prints every command it receives.

As a module, FakePrinter is a context manager: `port` is the device to connect
to and `received` lists the commands seen so far.
"""
from __future__ import annotations

import grp
import os
import pty
import select
import sys
import threading
import time
import tty

REPLIES = {
    "M105": "ok T:21.0 /0.0 B:20.0 /0.0",
    "M114": "X:0.00 Y:0.00 Z:0.00 E:0.00 Count X:0 Y:0 Z:0\nok",
    "M115": "FIRMWARE_NAME:FakeFirmware PROTOCOL_VERSION:1.0\nok",
}


def strip_line(raw: str) -> str:
    """Remove the line number (N123) and checksum (*45) the host adds while printing."""
    line = raw.split(";", 1)[0].split("*", 1)[0].strip()
    if line.startswith("N") and " " in line:
        line = line.split(" ", 1)[1].strip()
    return line


class FakePrinter:
    def __init__(self, answers: bool = True, move_seconds: float = 0.0, echo: bool = False):
        self.answers = answers  # False: a port nothing listens on (printer off, wrong baud rate)
        self.move_seconds = move_seconds  # how long each G0/G1 takes
        self.echo = echo
        self.received: list[str] = []
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        if self.answers:
            os.write(self.master, b"start\n")
        buffer = b""
        while not self.stopped.is_set():
            if not select.select([self.master], [], [], 0.1)[0]:
                continue
            buffer += os.read(self.master, 4096)
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                command = strip_line(raw.decode(errors="replace"))
                if not command:
                    continue
                self.received.append(command)
                if self.echo:
                    print(command, flush=True)
                if command.split()[0] in ("G0", "G1"):
                    time.sleep(self.move_seconds)
                if self.answers:
                    os.write(self.master, (REPLIES.get(command.split()[0], "ok") + "\n").encode())

    def __enter__(self) -> FakePrinter:
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stopped.set()
        self.thread.join()
        os.close(self.master)
        os.close(self.slave)


def main() -> None:
    link = sys.argv[1]
    printer = FakePrinter(echo=True)
    for group in ("dialout", "uucp"):
        try:
            os.chown(printer.port, -1, grp.getgrnam(group).gr_gid)
            break
        except (KeyError, PermissionError):
            continue
    os.chmod(printer.port, 0o660)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(printer.port, link)
    print(f"fake printer on {printer.port} -> {link}", flush=True)
    try:
        printer.run()
    finally:
        os.remove(link)


if __name__ == "__main__":
    main()
