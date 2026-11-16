#!/bin/sh
# Install pronsoled as a systemd service whose commands need no sudo.
#
#   sudo ./install.sh              install or upgrade, then (re)start the service
#   sudo ./install.sh --uninstall  remove everything this script installed
set -eu
umask 022  # the service user must be able to read what root installs

PRINTRUN=printrun-2.2.0
PRINTRUN_SHA256=a676c64c2a628d3618359adfd940dc301220467be29df8605b41b9eb916dc607
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV=/opt/pronsoled/venv
BIN=/usr/local/bin/pronsoled
UNIT=/etc/systemd/system/pronsoled.service

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

case "${1:-}" in
    "") ;;
    --uninstall)
        systemctl disable --now pronsoled 2>/dev/null || true
        rm -rf "$UNIT" "$BIN" /opt/pronsoled /var/lib/pronsoled
        systemctl daemon-reload
        userdel pronsoled 2>/dev/null || true
        groupdel pronsoled 2>/dev/null || true
        echo "Removed pronsoled."
        exit 0
        ;;
    *)
        sed -n '2,5p' "$0" >&2
        exit 2
        ;;
esac

# Debian and Ubuntu ship Python without venv support.
if ! python3 -c 'import ensurepip' 2>/dev/null; then
    echo "Installing python3-venv..."
    apt-get update -qq && apt-get install -y -qq python3-venv
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Printrun's pronsole, as plain Python source. Installing it with pip would need
# a C compiler on newer Pythons (PyPI has no prebuilt package for them) and pull
# in wxPython; pronsole needs neither (see pronsoled/_pronsole.py).
echo "Downloading $PRINTRUN..."
python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(*sys.argv[1:])' \
    "https://files.pythonhosted.org/packages/source/p/printrun/$PRINTRUN.tar.gz" "$TMP/$PRINTRUN.tar.gz"
echo "$PRINTRUN_SHA256  $TMP/$PRINTRUN.tar.gz" | sha256sum --check --quiet
tar -xzf "$TMP/$PRINTRUN.tar.gz" -C "$TMP"

# A fresh, isolated environment: nothing installed elsewhere on the system can interfere.
echo "Installing into $VENV..."
python3 -m venv --clear "$VENV"
"$VENV/bin/pip" install --quiet pyserial platformdirs
SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
cp -r "$SRC/pronsoled" "$TMP/$PRINTRUN/printrun" "$SITE/"
"$VENV/bin/python" -W ignore -m compileall -q "$SITE/pronsoled" "$SITE/printrun" || true
# -I: ignore the working directory and the caller's Python settings.
printf '#!/bin/sh\nexec %s/bin/python -I -m pronsoled.cli "$@"\n' "$VENV" >"$BIN"
chmod 755 "$BIN"

# The service user, with access to serial ports (group uucp on Arch), and the
# group that may use the socket.
getent group pronsoled >/dev/null || groupadd --system pronsoled
id pronsoled >/dev/null 2>&1 ||
    useradd --system --gid pronsoled --home-dir /var/lib/pronsoled --shell /usr/sbin/nologin pronsoled
install -d -o pronsoled -g pronsoled -m 750 /var/lib/pronsoled
for group in dialout uucp; do
    ! getent group "$group" >/dev/null || usermod -aG "$group" pronsoled
done
[ -z "${SUDO_USER:-}" ] || usermod -aG pronsoled "$SUDO_USER"

# Prove that pronsole starts as the service user: the service itself may fail
# to start for an innocent reason, such as no printer being connected.
if ! (cd / && runuser -u pronsoled -- env HOME=/var/lib/pronsoled \
        "$VENV/bin/python" "$SITE/pronsoled/_pronsole.py") </dev/null >"$TMP/check.log" 2>&1; then
    cat "$TMP/check.log" >&2
    echo "ERROR: pronsole does not start as the 'pronsoled' user (see above)." >&2
    exit 1
fi

install -m 644 "$SRC/pronsoled.service" "$UNIT"
systemctl daemon-reload
systemctl enable --quiet pronsoled
echo ""
if systemctl restart pronsoled; then
    echo "pronsoled is installed and running."
else
    echo "pronsoled is installed but could not start: is the printer connected and switched on?"
    echo "It retries every 10 seconds. To see why it fails: journalctl -u pronsoled -n 20"
fi
if [ -n "${SUDO_USER:-}" ]; then
    echo "'$SUDO_USER' is now in the 'pronsoled' group. Log out and back in, then use"
    echo "pronsoled without sudo, for example: pronsoled status"
fi
