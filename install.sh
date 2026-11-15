#!/bin/sh
# Install pronsoled.
#
#   sudo ./install.sh                  system install: /opt/pronsoled + systemd service
#                                      running as the unprivileged 'pronsoled' user
#   ./install.sh                       user install: ~/.local, no root needed
#
# Options:
#   --with-printrun    also install Printrun's pronsole into pronsoled's venv
#   --uninstall        remove a previous install of the same kind
set -eu

# A --system-site-packages venv also sees the installing user's ~/.local packages.
# pip would then treat a `pip install --user printrun` (root's, or yours if sudo
# keeps HOME) as already installed, and the service user, who cannot see that
# directory, gets "No module named printrun". Keep ~/.local out of it.
export PYTHONNOUSERSITE=1

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
SERVICE_USER=pronsoled
WITH_PRINTRUN=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --with-printrun) WITH_PRINTRUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    MODE=system
    APP_DIR=/opt/pronsoled
    BIN_DIR=/usr/local/bin
else
    MODE=user
    APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pronsoled"
    BIN_DIR="$HOME/.local/bin"
    USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
fi
VENV="$APP_DIR/venv"

have() { command -v "$1" >/dev/null 2>&1; }

# The group that owns serial devices: dialout on Debian/Ubuntu/Fedora, uucp on Arch.
serial_group() {
    for dev in /dev/ttyACM* /dev/ttyUSB*; do
        [ -e "$dev" ] && { stat -c %G "$dev"; return; }
    done
    for group in dialout uucp; do
        getent group "$group" >/dev/null && { echo "$group"; return; }
    done
}

uninstall() {
    if [ "$MODE" = system ]; then
        if have systemctl; then
            systemctl disable --now pronsoled 2>/dev/null || true
        fi
        rm -f /etc/systemd/system/pronsoled.service
        have systemctl && systemctl daemon-reload || true
        rm -f "$BIN_DIR/pronsoled"
        rm -rf "$APP_DIR"
        echo "Removed pronsoled. The '$SERVICE_USER' user and /var/lib/pronsoled were kept;"
        echo "remove them with (after 'gpasswd -d <user> $SERVICE_USER' for each member):"
        echo "  userdel $SERVICE_USER && rm -rf /var/lib/pronsoled /var/log/pronsoled"
    else
        if have systemctl; then
            systemctl --user disable --now pronsoled 2>/dev/null || true
        fi
        rm -f "$USER_UNIT_DIR/pronsoled.service"
        have systemctl && systemctl --user daemon-reload 2>/dev/null || true
        rm -f "$BIN_DIR/pronsoled"
        rm -rf "$APP_DIR"
        echo "Removed pronsoled from $APP_DIR."
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall
    exit 0
fi

# --- Python environment -------------------------------------------------------
# A dedicated venv avoids PEP 668 "externally managed environment" errors.
# --system-site-packages lets it use a Printrun installed from the distro.
if ! "$PYTHON_BIN" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    if [ "$MODE" = system ] && have apt-get; then
        echo "Installing python3-venv..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
    else
        echo "ERROR: $PYTHON_BIN cannot create virtual environments." >&2
        echo "Install the venv module first (Debian/Ubuntu: sudo apt install python3-venv)." >&2
        exit 1
    fi
fi

echo "Installing pronsoled into $VENV ($MODE install)..."
mkdir -p "$APP_DIR" "$BIN_DIR"
"$PYTHON_BIN" -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SCRIPT_DIR"
if [ "$WITH_PRINTRUN" -eq 1 ]; then
    # Printrun imports wxPython even for pronsole, and PyPI has no Linux wheels for
    # it (building takes ages). Use the distro's prebuilt wxPython instead; the
    # venv sees it through --system-site-packages.
    if ! "$VENV/bin/python" -c 'import wx' >/dev/null 2>&1; then
        if [ "$MODE" = system ] && have apt-get; then
            echo "Installing wxPython from apt (python3-wxgtk4.0)..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-wxgtk4.0
        else
            echo "ERROR: Printrun needs wxPython. Install it from your distro first, e.g." >&2
            echo "  sudo apt install python3-wxgtk4.0    (Debian/Ubuntu)" >&2
            echo "  sudo dnf install python3-wxpython4   (Fedora)" >&2
            echo "  sudo pacman -S python-wxpython       (Arch)" >&2
            exit 1
        fi
    fi
    echo "Installing Printrun..."
    "$VENV/bin/python" -m pip install --quiet --upgrade printrun
fi
ln -sf "$VENV/bin/pronsoled" "$BIN_DIR/pronsoled"

# --- Service ------------------------------------------------------------------
SERIAL_GROUP=$(serial_group || true)

if [ "$MODE" = system ]; then
    if ! getent group "$SERVICE_USER" >/dev/null; then
        groupadd --system "$SERVICE_USER"
    fi
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --gid "$SERVICE_USER" --home-dir /var/lib/pronsoled \
            --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
    if [ -n "$SERIAL_GROUP" ]; then
        usermod -aG "$SERIAL_GROUP" "$SERVICE_USER"
    else
        echo "WARNING: no serial group (dialout/uucp) found; the daemon may not open the printer." >&2
    fi

    # Let the person who ran sudo use the CLI without sudo.
    CLIENT_USER=${SUDO_USER:-}
    if [ -n "$CLIENT_USER" ] && [ "$CLIENT_USER" != root ]; then
        usermod -aG "$SERVICE_USER" "$CLIENT_USER"
    fi

    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 /var/lib/pronsoled
    if have systemctl; then
        install -Dm644 "$SCRIPT_DIR/pronsoled.service" /etc/systemd/system/pronsoled.service
        systemctl daemon-reload
    fi

    # Start pronsole exactly as the service will (same user, HOME and Python).
    echo "Checking that pronsole starts as the '$SERVICE_USER' user..."
    if ! CHECK=$(cd /var/lib/pronsoled && runuser -u "$SERVICE_USER" -- \
            env HOME=/var/lib/pronsoled "$BIN_DIR/pronsoled" check 2>&1); then
        echo "$CHECK" >&2
        echo "" >&2
        if [ "$WITH_PRINTRUN" -eq 0 ]; then
            echo "ERROR: pronsole is not usable by the service. Rerun: sudo ./install.sh --with-printrun" >&2
        else
            echo "ERROR: pronsole is not usable by the service (see above)." >&2
        fi
        exit 1
    fi
    echo "$CHECK" | head -2

    echo ""
    echo "Installed: $BIN_DIR/pronsoled -> $VENV/bin/pronsoled"
    echo "Start the daemon (and at boot) with:"
    echo "  sudo systemctl enable --now pronsoled"
    if [ -n "$CLIENT_USER" ] && [ "$CLIENT_USER" != root ]; then
        echo ""
        echo "Added '$CLIENT_USER' to the '$SERVICE_USER' group. Log out and back in"
        echo "(or run 'newgrp $SERVICE_USER'), then use pronsoled without sudo:"
        echo "  pronsoled status"
    fi
    echo "Other users can be given access with: sudo usermod -aG $SERVICE_USER <user>"
else
    if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
        mkdir -p "$USER_UNIT_DIR"
        cat > "$USER_UNIT_DIR/pronsoled.service" <<EOF
[Unit]
Description=pronsoled - persistent Printrun pronsole daemon (user)
Documentation=https://github.com/silvieon/pronsoled

[Service]
Type=notify
NotifyAccess=main
TimeoutStartSec=60
Environment=PYTHONUNBUFFERED=1
ExecStart=$BIN_DIR/pronsoled start auto 115200
Restart=on-failure
RestartSec=10
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        UNIT_NOTE="systemctl --user enable --now pronsoled"
    else
        UNIT_NOTE="pronsoled start auto 115200"
    fi

    if ! CHECK=$("$BIN_DIR/pronsoled" check 2>&1); then
        echo "$CHECK" >&2
        echo "ERROR: pronsole does not start. Rerun with --with-printrun (see README)." >&2
        exit 1
    fi

    echo ""
    echo "Installed: $BIN_DIR/pronsoled -> $VENV/bin/pronsoled"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "NOTE: $BIN_DIR is not on your PATH; add it to your shell profile." ;;
    esac
    if [ -n "$SERIAL_GROUP" ] && ! id -nG | tr ' ' '\n' | grep -qx "$SERIAL_GROUP"; then
        echo "NOTE: you are not in the '$SERIAL_GROUP' group, so the printer port is not accessible."
        echo "      Run 'sudo usermod -aG $SERIAL_GROUP $USER' once, then log in again."
    fi
    echo "Start the daemon with:"
    echo "  $UNIT_NOTE"
fi
