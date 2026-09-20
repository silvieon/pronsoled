#!/usr/bin/env python3
import sys

for line in sys.stdin:
    command = line.strip()
    if command.startswith("connect "):
        print("Printer is now online", flush=True)
    elif command == "eta":
        print("Printer is not currently printing", flush=True)
    elif command.startswith("load "):
        print("Loaded file", flush=True)
    elif command == "print":
        print("Print job started", flush=True)
    elif command == "pause":
        print("Print paused", flush=True)
    else:
        print(f"echo: {command}", flush=True)
