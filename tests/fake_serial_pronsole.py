import os
import select
import sys
import termios
import tty


serial_fd = None


def serial_command(command: str) -> str:
    os.write(serial_fd, (command + "\n").encode())
    ready, _, _ = select.select([serial_fd], [], [], 2)
    if not ready:
        return ""
    return os.read(serial_fd, 4096).decode(errors="replace").strip()


for line in sys.stdin:
    command = line.strip()
    if command.startswith("connect "):
        _, port, _baud = command.split(maxsplit=2)
        serial_fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(serial_fd)
        print("Printer is now online", flush=True)
        continue

    response = serial_command(command)
    if response:
        print(response, flush=True)
