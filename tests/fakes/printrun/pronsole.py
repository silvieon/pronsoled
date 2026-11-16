"""A stand-in for printrun.pronsole that prints what the real pronsole 2.2.0 prints.

tests/test_printrun.py checks those messages against the real thing. The port
given to `connect` picks the scenario:

    .../missing  the port cannot be opened
    .../silent   the port opens, but no printer ever answers
    .../crash    pronsole dies
    anything else: a printer that comes online at once

Lines starting with "[fake]" and these commands exist only here: `die`,
`unplug` (the printer goes away), `sleep SECONDS` (a slow command, such as
loading a large file), `later SECONDS TEXT` (the printer says TEXT later) and
`lines N` (a long reply).
"""
import os
import sys
import threading
import time

# The real printrun.settings does this at import time. pronsoled's launcher must
# make it work without wxPython.
import wx  # noqa: F401
from .gui.widgets import get_space

get_space("settings")

print("WARNING:root:Memory-efficient GCoder implementation unavailable", flush=True)


def say(text: str) -> None:
    try:
        print(text, flush=True)
    except OSError:
        pass  # like the real one, which prints through logging: a closed pipe is not fatal


def later(seconds: float, text: str) -> None:
    timer = threading.Timer(seconds, say, ["\r" + text])  # the real one starts printer messages with \r
    timer.daemon = True
    timer.start()


class pronsole:
    def __init__(self) -> None:
        self.promptstrs = {"offline": "\033[01moffline>\033[00m ", "online": "\033[01mfake 21°>\033[00m "}
        self.p = self
        self.online = False
        self.filename = None
        self.printing = False
        self.statuscheck = False
        self.status_thread = None

    def parse_cmdline(self, args: list[str]) -> None:
        pass

    def disconnect(self) -> None:
        self.online = False
        say("[fake] disconnected")

    def kill(self) -> None:
        self.statuscheck = False
        if self.status_thread:
            self.status_thread.join()
            self.status_thread = None
        say("[fake] status thread stopped")

    def statuschecker(self) -> None:
        # Like the real one: not a daemon thread, so it keeps the process alive
        # until kill() is called.
        while self.statuscheck:
            time.sleep(0.05)

    def cmdloop(self) -> None:
        say('Welcome to the printer console! Type "help" for a list of available commands.')
        say(f"[fake] pid {os.getpid()}")
        while True:
            try:
                line = input(self.promptstrs["online" if self.online else "offline"])
            except EOFError:
                say("Exiting program. Goodbye!")
                self.disconnect()
                self.kill()
                sys.exit()
            self.onecmd(line.strip())

    def onecmd(self, line: str) -> None:
        name, _, argument = line.partition(" ")
        if name == "connect":
            port, baud = argument.split()
            say(f"[fake] connect {port} {baud}")
            if port.endswith("crash"):
                say("fake pronsole crashing")
                os._exit(3)
            elif port.endswith("missing"):
                say("stty: No such file or directory")
                say(f"Connection error: Could not connect to serial port '{port}'")
            elif not port.endswith("silent"):
                self.online = True
                self.statuscheck = True
                self.status_thread = threading.Thread(target=self.statuschecker)
                self.status_thread.start()
                say("\rPrinter is now online")
        elif name == "disconnect":
            self.online = False
        elif name == "eta":
            if self.printing:
                say("Est: 0:00:10 of 0:01:00 remaining")
            else:
                say("Printer is not currently printing. No ETA available.")
        elif name == "load":
            say(f"Loading file: {argument}")
            if not os.path.exists(argument):
                say("File not found!")
                return
            with open(argument, encoding="utf-8") as gcode:
                count = len(gcode.readlines())  # like the real one, fails on a file that is not text
            self.filename = argument
            say(f"Loaded {argument}, {count} lines.")
            say("Estimated duration: 1 layers, 0:00:00")
        elif name == "print":
            if not self.filename:
                say("No file loaded. Please use load first.")
            elif not self.online:
                say("Not connected to printer.")
            else:
                self.printing = True
                say(f"Printing {self.filename}")
                say("You can monitor the print with the monitor command.")
        elif name == "pause":
            if self.printing:
                self.printing = False
            else:
                say("Not printing, cannot pause.")
        elif name == "die":
            say("fake pronsole crashing")
            os._exit(3)
        elif name == "unplug":
            say("Can't read from printer (disconnected?) Unable to read from serial port '/dev/fake'")
        elif name == "sleep":
            time.sleep(float(argument))
            say("slept")
        elif name == "later":
            seconds, text = argument.split(" ", 1)
            later(float(seconds), text)
        elif name == "lines":
            for number in range(int(argument)):
                say(f"line {number}")
                time.sleep(0.02)
        elif line[:1].upper() in tuple("MGTD$"):
            if not self.online:
                say("Printer is not online.")
                return
            say("SENDING:" + line.upper())
            if line.upper() == "M105":
                later(0.02, "ok T:21.0 /0.0 B:20.0 /0.0")
        elif line:
            say(f"*** Unknown syntax: {line}")
