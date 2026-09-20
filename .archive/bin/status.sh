#!/bin/bash

PIPE="/tmp/pronsole/commands"
LOG="/tmp/pronsole/output.log"

if [ ! -p "$PIPE" ]; then
    echo "ERROR: Pronsole daemon not running"
    exit 1
fi

# Get current log size
LOG_START=$(wc -l < "$LOG")

# Send command
echo "eta" > "$PIPE"

# Wait for response
sleep 1

# Get new lines (the response)
LOG_END=$(wc -l < "$LOG")

# Extract the response lines
tail -n +$((LOG_START + 1)) "$LOG" | head -n $((LOG_END - LOG_START))
