#!/usr/bin/env python3
"""A fake Marlin-style printer on a pseudo-terminal, for testing without hardware.

Usage (as root, so the pty can be handed to the serial group):
    fake_firmware.py /dev/ttyFAKE0

Creates a pty, symlinks it to the given path, makes it readable/writable by the
dialout (or uucp) group, and answers G-code the way firmware would.
"""
from __future__ import annotations

import grp
import os
import pty
import select
import sys
import tty

RESPONSES = {
    "M105": "ok T:21.0 /0.0 B:20.0 /0.0",
    "M114": "X:0.00 Y:0.00 Z:0.00 E:0.00 Count X:0 Y:0 Z:0\nok",
    "M115": "FIRMWARE_NAME:FakeFirmware PROTOCOL_VERSION:1.0\nok",
}


def strip_line(raw: str) -> str:
    """Remove the line number (N123) and checksum (*45) printcore adds."""
    line = raw.split(";", 1)[0].split("*", 1)[0].strip()
    if line.startswith("N") and " " in line:
        line = line.split(" ", 1)[1].strip()
    return line


def main() -> None:
    link = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ttyFAKE0"
    master, slave = pty.openpty()
    tty.setraw(slave)
    name = os.ttyname(slave)
    for group in ("dialout", "uucp"):
        try:
            os.chown(name, -1, grp.getgrnam(group).gr_gid)
            break
        except (KeyError, PermissionError):
            continue
    os.chmod(name, 0o660)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(name, link)
    print(f"fake printer on {name} -> {link}", flush=True)

    os.write(master, b"start\n")
    buffer = b""
    while True:
        select.select([master], [], [])
        buffer += os.read(master, 4096)
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            command = strip_line(raw.decode(errors="replace"))
            if not command:
                continue
            reply = RESPONSES.get(command.split()[0], "ok")
            os.write(master, (reply + "\n").encode())


if __name__ == "__main__":
    main()
