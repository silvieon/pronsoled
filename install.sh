#!/bin/bash

set -e

echo "=== pronsoled installation for Ubuntu Server ==="

# Check if running as sudo (not required but recommended)
if [ "$EUID" -ne 0 ]; then
   echo "WARNING: Running without sudo. You may need elevated permissions for some operations."
   INSTALL_PREFIX="$HOME/.local/bin"
   mkdir -p "$INSTALL_PREFIX"
else
   INSTALL_PREFIX="/usr/local/bin"
fi

echo "Install prefix: $INSTALL_PREFIX"

# Check dependencies
echo ""
echo "Checking dependencies..."

if ! command -v pronsole.py &> /dev/null; then
   echo "ERROR: pronsole.py not found in PATH"
   echo "Please install Printrun first:"
   echo "  sudo apt-get install printrun"
   echo "  or: pip install printrun"
   exit 1
fi

echo "✓ pronsole.py found"

# Check for bash (should be default on Ubuntu)
if ! command -v bash &> /dev/null; then
   echo "ERROR: bash not found (this should not happen on Ubuntu Server)"
   exit 1
fi

echo "✓ bash found"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install binaries to PATH
echo ""
echo "Installing pronsoled commands to $INSTALL_PREFIX..."

for script in bin/*.sh; do
   script_name=$(basename "$script" .sh)
   
   # Create wrapper that calls the actual script
   # This ensures PATH resolution works correctly
   cat > "$INSTALL_PREFIX/pronsoled-$script_name" << EOF
#!/bin/bash
exec "$SCRIPT_DIR/bin/$(basename "$script")" "\$@"
EOF
   chmod +x "$INSTALL_PREFIX/pronsoled-$script_name"
   echo "  ✓ pronsoled-$script_name"
done

# Optional: Create convenience symlinks for common commands
ln -sf "$INSTALL_PREFIX/pronsoled-start_pronsoled" "$INSTALL_PREFIX/pronsoled" || true
echo "  ✓ pronsoled (symlink to pronsoled-start_pronsoled)"

# Optionally install systemd service
echo ""
echo "Would you like to install a systemd service for auto-start? (y/n)"
read -r install_service

if [ "$install_service" = "y" ] || [ "$install_service" = "Y" ]; then
   if [ "$EUID" -ne 0 ]; then
      echo "ERROR: systemd service installation requires sudo"
      echo "Please run: sudo bash install.sh"
      exit 1
   fi
   
   SERVICE_FILE="/etc/systemd/system/pronsoled.service"
   
   cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=Pronsoled - Stable Pronsole Daemon for 3D Printer
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/pronsoled
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
   
   chmod 644 "$SERVICE_FILE"
   systemctl daemon-reload
   systemctl enable pronsoled.service
   echo "✓ systemd service installed and enabled"
   echo "  Start with: sudo systemctl start pronsoled"
   echo "  Status:     sudo systemctl status pronsoled"
   echo "  Logs:       sudo journalctl -u pronsoled -f"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Available commands:"
echo "  pronsoled                     - Start the daemon"
echo "  pronsoled-send_command        - Send a pronsole command"
echo "  pronsoled-print_status        - Get printer status"
echo "  pronsoled-start_print         - Start a print job"
echo "  pronsoled-abort_print         - Pause the current print"
echo ""
echo "For usage, see README.md"
