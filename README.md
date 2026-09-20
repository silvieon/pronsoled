# pronsoled

A stable daemon wrapper for [pronsole](https://github.com/kliment/Printrun) (Printrun's command-line interface) that solves the multi-instance serial contention issue on 3D printers.

## The Problem

Running multiple instances of pronsole against the same serial port causes intermittent print pauses and serial communication glitches. This happens because each instance competes for exclusive access to the USB serial device, leading to write collisions and dropped packets.

**pronsoled** solves this by maintaining a **single persistent pronsole instance** and routing all commands through it via Unix sockets. This guarantees:
- **One connection per printer** — eliminates serial contention
- **Stable printing** — no unexpected pauses or glitches
- **Multiple clients** — different tools/scripts can send commands simultaneously without interfering

## How It Works

```
Client 1 ──┐
           ├──> Unix socket ──> pronsole daemon ──> /dev/ttyACM1
Client 2 ──┤                    (persistent)       (USB serial)
Client N ──┘
```

All clients connect to a single Unix socket; the daemon maintains one pronsole instance. Responses are returned directly over the socket.

## Requirements

- Python 3.10+
- Printrun (installed via pip)
- A user with access to the printer serial device (usually through the `dialout` group)
- systemd (for automatic startup)

## Installation

### Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/pronsoled.git
cd pronsoled
sudo ./install.sh
```

The installer will:
1. Install the Python package to `/usr/local`
2. Create the `pronsoled` command in `/usr/local/bin/`
3. Install the systemd service (when run with sudo)

### Manual Installation

```bash
cd pronsoled
sudo python3 -m pip install --upgrade --break-system-packages .
```

## Setup

### 1. Ensure Printrun is Installed

```bash
sudo python3 -m pip install --upgrade --break-system-packages printrun
```

### 2. Create Config Directory

```bash
sudo mkdir -p /var/lib/pronsoled
sudo mkdir -p /root/.config/Printrun
```

### 3. Install & Enable Systemd Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pronsoled
```

## Usage

### Check Daemon Status

```bash
sudo systemctl status pronsoled
sudo pronsoled status
```

### Send Commands to Printer

```bash
sudo pronsoled send "M105"       # Get nozzle & bed temperature
sudo pronsoled send "eta"        # Get print status/ETA
sudo pronsoled send "M114"       # Get current position
```

### Start a Print Job

```bash
sudo pronsoled print /path/to/file.gcode
```

### Abort/Pause Current Print

```bash
sudo pronsoled abort
```

### Manual Daemon Control

```bash
# Start daemon on specific port
sudo pronsoled start /dev/ttyACM1 115200

# Auto-detect printer port
sudo pronsoled start auto 115200
```

## Configuration

The systemd service is configured in `/etc/systemd/system/pronsoled.service`:

```ini
[Service]
ExecStart=/usr/local/bin/pronsoled start auto 115200
Environment=PRONSOLED_CONFIG_DIR=/var/lib/pronsoled
WorkingDirectory=/var/lib/pronsoled
```

To change the port or baud rate, edit the service file and restart:

```bash
sudo systemctl edit pronsoled
sudo systemctl restart pronsoled
```

## Example Workflow

```bash
# Start the daemon (if not running via systemd)
$ sudo pronsoled start auto 115200
Starting pronsole daemon
  Input:  /tmp/pronsole/commands
  Output: /tmp/pronsole/output.log
  Port:   /dev/ttyACM1 @ 115200
Printer connected successfully

# Check status
$ sudo pronsoled status
ttyACM1 22°> Printer is not currently printing. No ETA available.

# Heat up nozzle
$ sudo pronsoled send "M109 S200"
Setting nozzle temp to 200C...

# Start a print
$ sudo pronsoled print model.gcode
Print job started

# Check progress
$ sudo pronsoled status
ttyACM1 45°> Printing model.gcode - ETA 45 minutes
```

## Integration

### From Shell Scripts

```bash
#!/bin/bash
if sudo pronsoled status | grep -q "not currently printing"; then
    sudo pronsoled print model.gcode
else
    echo "Printer busy"
fi
```

### From Python

```python
import subprocess

def send_command(cmd):
    result = subprocess.run(["sudo", "pronsoled", "send", cmd], 
                          capture_output=True, text=True)
    return result.stdout.strip()

def get_status():
    return send_command("eta")

print(get_status())
```

## Troubleshooting

### "permission denied" on socket

The daemon is running as root but you're accessing it as a regular user. Either:
- Use `sudo` for all commands: `sudo pronsoled status`
- Or run the daemon as your user (not recommended for systemd)

### Daemon won't start

Check systemd logs:

```bash
sudo journalctl -u pronsoled -n 50
sudo systemctl status pronsoled
```

Common issues:
- `/root/.config/Printrun` doesn't exist — create it: `sudo mkdir -p /root/.config/Printrun`
- `/var/lib/pronsoled` doesn't exist — create it: `sudo mkdir -p /var/lib/pronsoled`
- Printer not connected — verify: `ls /dev/ttyACM*`

### "Printer is not currently printing" but daemon seems stuck

Try restarting:

```bash
sudo systemctl restart pronsoled
sleep 2
sudo pronsoled status
```

## Project Structure

```
pronsoled/
├── pronsoled/
│   ├── cli.py           # Command-line interface
│   ├── daemon.py        # Daemon server & pronsole wrapper
│   └── __init__.py
├── tests/               # Test suite
├── pyproject.toml       # Python package metadata
├── pronsoled.service    # systemd unit file
├── install.sh           # Installation script
├── README.md
└── LICENSE (MIT)
```

## Version History

### v0.1.1
- Rewrite from bash to Python using asyncio
- Improved stability with persistent socket connection
- Fixed systemd service integration
- Better error handling and logging

### v0.1.0
- Initial bash-based implementation

## License

MIT License — See LICENSE file for details.

## Contributing

Found a bug? Have a feature request? Open an issue or submit a PR!

## Credits

- Built for [Printrun/pronsole](https://github.com/kliment/Printrun) by Kliment Yanev
- Developed with help from Claude (Anthropic)