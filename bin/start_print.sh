#!/bin/bash

GCODE_FILE="$1"
PIPE="/tmp/pronsole/commands"
LOG="/tmp/pronsole/output.log"

if [ -z "$GCODE_FILE" ]; then
    echo "ERROR: Usage: start_print.sh <gcode_file>"
    exit 1
fi

if [ ! -f "$GCODE_FILE" ]; then
    echo "ERROR: File not found: $GCODE_FILE"
    exit 1
fi

if [ ! -p "$PIPE" ]; then
    echo "ERROR: Pronsole daemon not running"
    exit 1
fi

# Check if printer is busy
STATUS=$(bash "$(dirname "$0")/print_status.sh")

if echo "$STATUS" | grep -q "not currently printing"; then
    echo "Printer idle, starting print..."
    echo "load $GCODE_FILE" > "$PIPE"
    sleep 1
    echo "print" > "$PIPE"
    echo "Print job started"
else
    echo "ERROR: Printer is already printing"
    exit 1
fi
