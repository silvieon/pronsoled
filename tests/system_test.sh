#!/bin/bash
# End-to-end test of install.sh and the service on a real systemd machine, with
# a fake printer.
#
#   sudo tests/system_test.sh
#
# Use a THROWAWAY machine (a VM, a cloud instance, a WSL instance with systemd).
# The test installs pronsoled, creates users, and removes all of it again. It
# refuses to run where pronsoled is already installed.
set -u
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi
if [ ! -d /run/systemd/system ]; then
    echo "This machine does not run systemd." >&2
    exit 1
fi
if [ -e /opt/pronsoled ] || [ -e /etc/systemd/system/pronsoled.service ] || id pronsoled >/dev/null 2>&1; then
    echo "pronsoled is installed here, and this test would remove it. Use a throwaway machine." >&2
    exit 1
fi

PORT=/dev/ttyFAKE0
MEMBER=pronsoled-test-member        # installs pronsoled, which puts them in the pronsoled group
COLLEAGUE=pronsoled-test-colleague  # added to the group later
OUTSIDER=pronsoled-test-outsider    # never in the group
PYTHON=/opt/pronsoled/venv/bin/python
DROPIN=/etc/systemd/system/pronsoled.service.d
WORK=$(mktemp -d)                   # holds a copy of the checkout that the test users can read
FAILED=0

check() {  # check DESCRIPTION COMMAND...: passes if the command succeeds
    local description=$1
    shift
    if "$@" >"$WORK/output" 2>&1; then
        echo "ok    $description"
    else
        echo "FAIL  $description"
        sed 's/^/          /' "$WORK/output"
        FAILED=$((FAILED + 1))
    fi
}

fails() {  # fails COMMAND...: succeeds if the command fails
    ! "$@"
}

says() {  # says TEXT COMMAND...: succeeds if the command's output contains TEXT
    local text=$1 output
    shift
    output=$("$@" 2>&1)
    printf '%s\n' "$output"
    printf '%s\n' "$output" | grep -qF -- "$text"
}

eventually() {  # eventually SECONDS COMMAND...: retries until the command succeeds
    local deadline=$((SECONDS + $1))
    shift
    until "$@"; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 0.5
    done
}

as() {  # as USER SHELL-COMMAND: runs it in a fresh login of that user, who has no sudo rights
    runuser -l "$1" -c "$2"
}

mark() {  # journal checks after this see only what is logged from now on
    MARK=$(date '+%Y-%m-%d %H:%M:%S')
    sleep 1
}

journal() {
    journalctl -u pronsoled --no-pager -o cat --since "$MARK"
}

printer_log() {
    journalctl -u pronsoled-test-printer --no-pager -o cat --since "$MARK"
}

start_printer() {
    systemd-run --quiet --collect --unit=pronsoled-test-printer python3 "$WORK/repo/tests/fake_firmware.py" "$PORT"
    eventually 10 test -e "$PORT"
}

property() {  # property NAME: a property of the pronsoled unit
    systemctl show -p "$1" --value pronsoled
}

stopped_cleanly() {
    [ "$(property ActiveState)" = inactive ] && [ "$(property Result)" = success ]
}

no_process() {
    ! pgrep -f '^/opt/pronsoled/'
}

run_tests() {  # run_tests PYTHON: the unit tests, as an ordinary user
    as "$MEMBER" "cd $WORK/repo && $1 -m unittest discover -s tests -v" 2>&1 | tee "$WORK/tests.log"
    return "${PIPESTATUS[0]}"
}

nothing_left() {
    local path
    for path in /opt/pronsoled /usr/local/bin/pronsoled /etc/systemd/system/pronsoled.service \
        /var/lib/pronsoled /run/pronsoled; do
        check "uninstall leaves no $path" test ! -e "$path"
    done
    check "uninstall leaves no pronsoled user" fails id pronsoled
    check "uninstall leaves no pronsoled group" fails getent group pronsoled
    check "uninstall leaves no process" no_process
    check "uninstall leaves no enabled unit" fails systemctl is-enabled pronsoled
}

cleanup() {
    systemctl stop pronsoled-test-printer 2>/dev/null
    [ ! -e /opt/pronsoled ] || ./install.sh --uninstall >/dev/null 2>&1
    rm -rf "$DROPIN" "$PORT" "$WORK"
    systemctl daemon-reload
    for user in "$MEMBER" "$COLLEAGUE" "$OUTSIDER"; do
        id "$user" >/dev/null 2>&1 || continue
        # `runuser -l` starts a systemd user session, which outlives the command.
        systemctl stop "user@$(id -u "$user").service" 2>/dev/null
        pkill -KILL -u "$user"
        userdel --remove "$user" 2>/dev/null || echo "WARNING: could not remove the test user $user" >&2
    done
}
trap cleanup EXIT

for user in "$MEMBER" "$COLLEAGUE" "$OUTSIDER"; do
    useradd --create-home --shell /bin/bash "$user" || exit 1
done
mkdir "$WORK/repo"
cp -r install.sh pronsoled.service README.md pronsoled tests "$WORK/repo/"
chmod -R a+rX "$WORK"

echo "== Unit tests before installing"
check "the tests pass with the system Python and the fake Printrun" run_tests python3

echo "== Installing"
mark
check "install.sh shows its usage for an unknown option" says "sudo ./install.sh --uninstall" ./install.sh --bogus
check "...and fails" fails ./install.sh --bogus
check "install.sh succeeds" env SUDO_USER="$MEMBER" ./install.sh
check "the installing user is in the pronsoled group" sh -c "id -nG $MEMBER | grep -qw pronsoled"
check "the service user is in the serial group" sh -c "id -nG pronsoled | grep -qwE 'dialout|uucp'"
check "wxPython was not installed" fails "$PYTHON" -c "import wx"
check "any user can run the command" says "pronsoled " as "$OUTSIDER" "pronsoled --version"
check "the command ignores Python files in the working directory" \
    as "$OUTSIDER" "cd \$(mktemp -d) && echo 'raise SystemExit(99)' >json.py && cp json.py socket.py && pronsoled --version"
check "systemd accepts the unit file" systemd-analyze verify /etc/systemd/system/pronsoled.service
check "the service is enabled at boot" systemctl is-enabled pronsoled
check "without a printer, the service is not running" fails systemctl is-active pronsoled
check "...the journal says why" says "expected exactly one /dev/ttyACM* port but found none" journal
check "...and so do clients" says "daemon is not running" as "$MEMBER" "pronsoled status"

echo "== Unit tests against the installed Printrun"
systemctl stop pronsoled  # its restart attempts would create /run/pronsoled now and then
check "the tests pass with the installed Python" run_tests "$PYTHON"
check "...including those against the real Printrun" grep -Eq '^test_messages .*RealPrintrunTest.* ok$' "$WORK/tests.log"

echo "== Service with a (fake) printer"
start_printer
mkdir -p "$DROPIN"
printf '[Service]\nExecStart=\nExecStart=/usr/local/bin/pronsoled start %s 115200\n' "$PORT" >"$DROPIN/port.conf"
systemctl daemon-reload
check "systemctl start succeeds" systemctl start pronsoled
check "status works at once, without sudo" says "not currently printing" as "$MEMBER" "pronsoled status"
check "the socket is pronsoled:pronsoled, mode 660" says "pronsoled:pronsoled 660" stat -c '%U:%G %a' /run/pronsoled/pronsoled.sock
check "its directory is setgid, mode 2770" says "pronsoled:pronsoled 2770" stat -c '%U:%G %a' /run/pronsoled
check "the daemon runs as the pronsoled user" says "pronsoled" stat -c %U "/proc/$(property MainPID)"
check "...with pronsole as its only other process" test "$(pgrep -cu pronsoled)" -eq 2
check "send reaches the printer" says "ok T:21.0 /0.0 B:20.0 /0.0" as "$MEMBER" "pronsoled send M105"

as "$MEMBER" "chmod 700 ~ && cp $WORK/repo/tests/test.gcode ~/part.gcode"
check "print works from a private home directory" says "Print job started" as "$MEMBER" "pronsoled print ~/part.gcode"
check "the printer receives the job" eventually 15 says "M84" printer_log
check "the staged job is readable by the daemon through its group only" \
    says "$MEMBER:pronsoled 640" stat -c '%U:%G %a' /run/pronsoled/job.gcode
check "the printer is idle again after the job" eventually 15 says "not currently printing" as "$MEMBER" "pronsoled status"
usermod -aG pronsoled "$COLLEAGUE"
as "$COLLEAGUE" "cp $WORK/repo/tests/test.gcode ~/other.gcode"
check "another member of the group can print next" says "Print job started" as "$COLLEAGUE" "pronsoled print ~/other.gcode"
check "...which replaces the first user's staged job" \
    says "$COLLEAGUE:pronsoled 640" stat -c '%U:%G %a' /run/pronsoled/job.gcode
check "a user outside the group is refused" fails as "$OUTSIDER" "pronsoled status"
check "...and told how to get access" says "sudo usermod -aG pronsoled" as "$OUTSIDER" "pronsoled status"

echo "== Logs"
check "the journal holds what pronsole prints" says "Printer is now online" journal
check "...and what the daemon prints" says "Ready: listening on /run/pronsoled/pronsoled.sock" journal
check "...without control characters (journalctl would show 'blob data')" fails says "blob data" journalctl -u pronsoled --no-pager
check "Printrun keeps its own log in the service user's home" grep -q "Printer is now online" /var/lib/pronsoled/printrun.log

echo "== Stopping, failing and recovering"
check "systemctl stop succeeds" systemctl stop pronsoled
check "...leaving the unit inactive, not failed" stopped_cleanly
check "...and no process" no_process
check "...and no socket directory" test ! -e /run/pronsoled

systemctl start pronsoled
mark
pkill -9 -u pronsoled -f _pronsole.py
check "when pronsole is killed, the daemon exits and says why" \
    eventually 10 says "pronsole exited unexpectedly with code -9" journal
check "...and systemd restarts it" eventually 30 as "$MEMBER" "pronsoled status"

mark
systemctl stop pronsoled-test-printer
check "when the printer is unplugged, the daemon exits and says why" eventually 10 says "lost the printer" journal
check "...and systemctl start fails while the printer is away" fails systemctl start pronsoled
check "...with the reason in the journal" says "Connection error: Could not connect to serial port '$PORT'" journal
start_printer
check "...and the service comes back by itself when the printer does" eventually 30 as "$MEMBER" "pronsoled status"

echo "== Upgrading, uninstalling, reinstalling"
check "running install.sh again upgrades in place" ./install.sh
check "...and the service is running afterwards" says "not currently printing" as "$MEMBER" "pronsoled status"
check "uninstall succeeds" ./install.sh --uninstall
nothing_left
check "a fresh install under a restrictive umask succeeds" sh -c "umask 077 && ./install.sh"
usermod -aG pronsoled "$MEMBER"
check "...and works for a member of the group" says "not currently printing" as "$MEMBER" "pronsoled status"
check "uninstall succeeds again" ./install.sh --uninstall
nothing_left

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed."
else
    echo "$FAILED check(s) FAILED."
fi
exit $((FAILED > 0))
