#!/bin/bash

PIPE="/tmp/pronsole/commands"

if [ ! -p "$PIPE" ]; then
    echo "ERROR: Pronsole daemon not running"
    exit 1
fi

echo "pause" > "$PIPE"
echo "Print paused"
