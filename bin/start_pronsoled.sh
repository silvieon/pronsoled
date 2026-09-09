#!/bin/bash

PIPE_DIR="/tmp/pronsole"
PIPE_IN="$PIPE_DIR/commands"
LOG_FILE="$PIPE_DIR/output.log"
PORT="${1:-auto}"
BAUD="${2:-115200}"

if [ "$PORT" = "auto" ]; then
    PORTS=$(ls /dev/ttyACM* 2>/dev/null || true)
    PORT_COUNT=$(echo "$PORTS" | wc -w)
    
    if [ "$PORT_COUNT" -eq 0 ]; then
        echo "ERROR: No ttyACM port found"
        exit 1
    elif [ "$PORT_COUNT" -gt 1 ]; then
        echo "ERROR: Multiple ttyACM ports found: $PORTS"
        exit 1
    fi
    
    PORT=$(echo "$PORTS" | awk '{print $1}')
fi

mkdir -p "$PIPE_DIR"
rm -f "$PIPE_IN"

mkfifo "$PIPE_IN"
> "$LOG_FILE"

echo "Starting pronsole daemon"
echo "  Input:  $PIPE_IN"
echo "  Output: $LOG_FILE"
echo "  Port:   $PORT @ $BAUD"

# Start pronsole and connect
{
    # Send initial commands
    echo "connect $PORT $BAUD"
    
    # Keep pipe open for future commands
    while true; do
        if read line < "$PIPE_IN"; then
            echo "$line"
        fi
    done
} | pronsole.py >> "$LOG_FILE" 2>&1 &

PRONSOLE_PID=$!

# Wait for connection
sleep 2

# Check if connection succeeded
if grep -q "Printer is now online" "$LOG_FILE"; then
    echo "Printer connected successfully"
    wait $PRONSOLE_PID
else
    echo "ERROR: Failed to connect to printer on $PORT"
    kill $PRONSOLE_PID 2>/dev/null || true
    rm -f "$PIPE_IN"
    exit 1
fi
