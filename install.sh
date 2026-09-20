#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ "$(id -u)" -eq 0 ]; then
    # System install to /usr/local
    PREFIX="/usr/local"
    PIP_ARGS="--target $PREFIX"
else
    # User install to ~/.local
    PREFIX="$HOME/.local"
    PIP_ARGS="--user"
fi

install_with_pip() {
    if "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
        $PYTHON_BIN -m pip install --upgrade $PIP_ARGS "$SCRIPT_DIR"
        return 0
    fi
    return 1
}

install_with_venv() {
    VENV_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pronsoled-install.XXXXXX")"
    trap 'rm -rf "$VENV_DIR"' EXIT INT TERM
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install --upgrade "$SCRIPT_DIR"
    install -Dm755 "$VENV_DIR/bin/pronsoled" "$PREFIX/bin/pronsoled"
}

if [ "$(id -u)" -eq 0 ]; then
    INSTALL_MODE="system"
else
    INSTALL_MODE="user"
fi

echo "Installing pronsoled into ${PREFIX} (${INSTALL_MODE} install)..."
if ! install_with_pip; then
    echo "pip is unavailable for ${PYTHON_BIN}; falling back to a temporary virtual environment."
    install_with_venv
fi

mkdir -p "$PREFIX/bin"
if [ ! -x "$PREFIX/bin/pronsoled" ]; then
    echo "ERROR: the pronsoled entry point was not installed at $PREFIX/bin/pronsoled" >&2
    exit 1
fi

if [ "${INSTALL_MODE}" = "system" ]; then
    if [ -f "$SCRIPT_DIR/pronsoled.service" ] && command -v systemctl >/dev/null 2>&1; then
        install -Dm644 "$SCRIPT_DIR/pronsoled.service" /etc/systemd/system/pronsoled.service
        systemctl daemon-reload
        echo "Installed the systemd unit. Enable it with:"
        echo "  systemctl enable --now pronsoled"
    fi
fi

echo "Installed CLI: $PREFIX/bin/pronsoled"
echo ""
echo "Usage:"
echo "  pronsoled /dev/ttyACM0 115200       # Start daemon on specific port"
echo "  pronsoled auto 115200               # Auto-detect printer port"
echo "  pronsoled                           # Auto-detect at default 115200 baud"
echo ""
echo "See README.md for more details."