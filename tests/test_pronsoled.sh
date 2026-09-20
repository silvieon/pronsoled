#!/bin/bash

# Test suite for pronsole print management

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

test_count=0
pass_count=0
fail_count=0

test_case() {
    local name="$1"
    test_count=$((test_count + 1))
    echo -e "${YELLOW}[TEST $test_count]${NC} $name"
}

pass() {
    pass_count=$((pass_count + 1))
    echo -e "${GREEN}✓ PASS${NC}\n"
}

fail() {
    local msg="$1"
    fail_count=$((fail_count + 1))
    echo -e "${RED}✗ FAIL: $msg${NC}\n"
}

# Test 1: Daemon starts
test_case "Daemon starts and creates pipes"
bash "$BIN_DIR/start_pronsoled.sh" &
DAEMON_PID=$!
sleep 2

if [ -p /tmp/pronsole/commands ] && [ -f /tmp/pronsole/output.log ]; then
    pass
else
    fail "Pipes not created"
fi

sleep 2

# Test 2: Send command
test_case "Send command via pipe"
bash "$BIN_DIR/send_command.sh" "M105" > /tmp/test_output.txt
if grep -q "T:" /tmp/test_output.txt; then
    pass
else
    fail "No temperature response"
fi

# Test 3: Get status
test_case "Get printer status"
STATUS=$(bash "$BIN_DIR/print_status.sh")
if echo "$STATUS" | grep -q "not currently printing\|print done in"; then
    pass
else
    fail "Invalid status response"
fi

# Test 4: Start print (requires valid gcode)
test_case "Start print with valid gcode"
TEST_GCODE="$SCRIPT_DIR/tests/test_minimal.gcode"
if [ -f "$TEST_GCODE" ]; then
    bash "$BIN_DIR/start_print.sh" "$TEST_GCODE" > /tmp/start_print.txt
    if grep -q "Print job started" /tmp/start_print.txt; then
        pass
    else
        fail "Print didn't start"
    fi
else
    echo -e "${YELLOW}(skipped - no test gcode found)${NC}"
fi

# Test 5: Abort print
test_case "Abort (pause) print"
bash "$BIN_DIR/abort_print.sh" > /tmp/abort_output.txt
if grep -q "paused" /tmp/abort_output.txt; then
    pass
else
    fail "Abort didn't work"
fi

# Cleanup
kill $DAEMON_PID 2>/dev/null || true
rm -f /tmp/pronsole/commands /tmp/pronsole/output.log

# Summary
echo "================================"
echo "Tests run: $test_count"
echo -e "Passed: ${GREEN}$pass_count${NC}"
echo -e "Failed: ${RED}$fail_count${NC}"
echo "================================"

if [ $fail_count -eq 0 ]; then
    exit 0
else
    exit 1
fi
