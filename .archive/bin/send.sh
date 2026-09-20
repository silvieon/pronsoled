#!/bin/bash

COMMAND="$1"
PIPE="/tmp/pronsole/commands"
LOG="/tmp/pronsole/output.log"

if [ -z "$COMMAND" ]; then
    echo "Usage: send_command.sh '<command>'"
    exit 1
fi

if [ ! -p "$PIPE" ]; then
    echo "ERROR: Pronsole daemon not running (pipe not found)"
    exit 1
fi

# Send command
echo "$COMMAND" > "$PIPE"

# Wait a moment for response
sleep 0.5

# Return last N lines of log
tail -5 "$LOG"
