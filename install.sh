#!/bin/sh
# Install pronsoled as a systemd service whose commands need no sudo.
#
#   sudo ./install.sh              install or upgrade
#   sudo ./install.sh --uninstall  remove
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
    "" | --with-printrun) ;;  # Printrun is always installed now; old flag still accepted
    --uninstall)
        systemctl disable --now pronsoled 2>/dev/null || true
        rm -rf "$UNIT" "$BIN" /opt/pronsoled
        systemctl daemon-reload
        echo "Removed pronsoled. Kept the 'pronsoled' user, /var/lib/pronsoled and /var/log/pronsoled."
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

# A fresh, isolated venv: nothing installed elsewhere on the system can interfere.
echo "Installing pronsoled into $VENV..."
python3 -m venv --clear "$VENV"
mkdir "$TMP/src"
cp -r "$SRC/pyproject.toml" "$SRC/pronsoled" "$TMP/src/"  # build outside the checkout
"$VENV/bin/pip" install --quiet "$TMP/src" pyserial platformdirs
ln -sf "$VENV/bin/pronsoled" "$BIN"

# Printrun's pronsole, as plain Python source. Installing it with pip would need
# a C compiler on newer Pythons (PyPI has no prebuilt package for them) and pull
# in wxPython; pronsole needs neither (see pronsoled/_pronsole.py).
echo "Installing $PRINTRUN..."
"$VENV/bin/python" -c 'import sys, urllib.request; urllib.request.urlretrieve(*sys.argv[1:])' \
    "https://files.pythonhosted.org/packages/source/p/printrun/$PRINTRUN.tar.gz" "$TMP/$PRINTRUN.tar.gz"
echo "$PRINTRUN_SHA256  $TMP/$PRINTRUN.tar.gz" | sha256sum --check --quiet
tar -xzf "$TMP/$PRINTRUN.tar.gz" -C "$TMP"
SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
cp -r "$TMP/$PRINTRUN/printrun" "$SITE/"
"$VENV/bin/python" -W ignore -m compileall -q "$SITE/printrun" || true

# The service user, with access to serial ports (group uucp on Arch).
getent group pronsoled >/dev/null || groupadd --system pronsoled
id pronsoled >/dev/null 2>&1 ||
    useradd --system --gid pronsoled --home-dir /var/lib/pronsoled --shell /usr/sbin/nologin pronsoled
install -d -o pronsoled -g pronsoled -m 750 /var/lib/pronsoled
for group in dialout uucp; do
    if getent group "$group" >/dev/null; then usermod -aG "$group" pronsoled; fi
done

install -m 644 "$SRC/pronsoled.service" "$UNIT"
systemctl daemon-reload

# Prove that pronsole starts as the service user before reporting success.
if ! (cd / && runuser -u pronsoled -- env HOME=/var/lib/pronsoled \
        "$VENV/bin/python" -m pronsoled._pronsole) </dev/null >"$TMP/check.log" 2>&1; then
    cat "$TMP/check.log" >&2
    echo "ERROR: pronsole does not start as the 'pronsoled' user (see above)." >&2
    exit 1
fi

echo ""
echo "Installed. Start the daemon now and at every boot with:"
echo "  sudo systemctl enable --now pronsoled"
echo "(after an upgrade: sudo systemctl restart pronsoled)"
if [ -n "${SUDO_USER:-}" ]; then
    usermod -aG pronsoled "$SUDO_USER"
    echo ""
    echo "Added '$SUDO_USER' to the 'pronsoled' group. Log out and back in, then run"
    echo "pronsoled without sudo, for example: pronsoled status"
fi
