# pronsoled

A stable daemon wrapper for [pronsole](https://github.com/kliment/Printrun) (Printrun's command-line interface) that solves the multi-instance serial contention issue on 3D printers.

## The Problem

Running multiple instances of pronsole against the same serial port causes intermittent print pauses and serial communication glitches. This happens because each instance competes for exclusive access to the USB serial device, leading to write collisions and dropped packets.

**pronsoled** solves this by maintaining a **single persistent pronsole instance** and routing all commands through it via named pipes. This guarantees:
- **One connection per printer** — eliminates serial contention
- **Stable printing** — no unexpected pauses or glitches
- **Multiple clients** — different tools/scripts can send commands simultaneously without interfering

## How It Works

```
Client 1 ──┐
           ├──> /tmp/pronsole/commands (named pipe) ──> pronsole daemon ──> /dev/ttyACM0
Client 2 ──┤                                                              (USB serial)
Client N ──┘
```

Each client writes to a shared named pipe; pronsole reads commands sequentially. Responses go to a log file that clients can tail.

## Requirements

- **Ubuntu Server 20.04+** (or any Ubuntu/Debian-based system)
- **bash** (pre-installed)
- **Printrun** (specifically `pronsole.py`)
- **Standard utilities**: `grep`, `awk`, `mkdir`, `mkfifo`, `wc`, etc. (pre-installed on Ubuntu)

## Installation

### Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/pronsoled.git
cd pronsoled
sudo bash install.sh
```

The installer will:
1. Check for `pronsole.py` in your PATH
2. Install pronsoled commands to `/usr/local/bin/` (or `~/.local/bin/` if not using sudo)
3. Optionally set up a systemd service for auto-start

### Manual Installation

If you prefer to install manually:

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/pronsoled.git
cd pronsoled

# Install commands to PATH
sudo cp -r bin/* /usr/local/bin/
# Or for user-only install:
# mkdir -p ~/.local/bin && cp -r bin/* ~/.local/bin/
# export PATH="$HOME/.local/bin:$PATH"

# Verify installation
pronsoled-send_command "M105"  # Should work once daemon is running
```

### Systemd Service (Optional)

For auto-start on boot:

```bash
sudo bash install.sh  # Select 'y' when prompted for systemd service
```

Or manually:

```bash
sudo cp pronsoled.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pronsoled
sudo systemctl start pronsoled
```

## Usage

### Starting the Daemon

```bash
pronsoled              # Default: auto-detect serial port, 115200 baud
pronsoled /dev/ttyACM0 115200  # Explicit port and baud rate
```

Once running, the daemon creates:
- `/tmp/pronsole/commands` — named pipe for incoming commands
- `/tmp/pronsole/output.log` — log of all responses

### Sending Commands

```bash
# Send a pronsole command
pronsoled-send_command "M105"  # Get temperature

# Get printer status
pronsoled-print_status

# Start a print job
pronsoled-start_print /path/to/file.gcode

# Pause/abort
pronsoled-abort_print
```

Each command returns the last few lines of the daemon's output log.

### Example Workflow

```bash
# Terminal 1: Start the daemon
$ pronsoled
Starting pronsole daemon
  Input:  /tmp/pronsole/commands
  Output: /tmp/pronsole/output.log
  Port:   /dev/ttyACM0 @ 115200
Printer connected successfully

# Terminal 2: Send commands
$ pronsoled-print_status
Printer is not currently printing
> 

$ pronsoled-send_command "M109 S200"
Setting nozzle temp to 200C...

$ pronsoled-start_print my_model.gcode
Printer idle, starting print...
Print job started

# Terminal 3: Check status anytime
$ pronsoled-print_status
Printing my_model.gcode
Estimated time: 45 minutes
```

## API/Integration

### From Shell Scripts

```bash
#!/bin/bash
# Check if printer is ready before printing
if pronsoled-print_status | grep -q "not currently printing"; then
    pronsoled-start_print model.gcode
else
    echo "Printer busy"
fi
```

### From Python

```python
import subprocess
import time

def send_pronsole_command(cmd):
    result = subprocess.run(["pronsoled-send_command", cmd], 
                          capture_output=True, text=True)
    return result.stdout

def get_printer_status():
    return send_pronsole_command("M119").strip()

# Usage
print(get_printer_status())
```

### From Other Languages

Any language with subprocess/shell capabilities can call the `pronsoled-*` commands directly.

## Troubleshooting

### "ERROR: Pronsole daemon not running"

The daemon isn't running. Start it first:

```bash
pronsoled
```

Or check if it's running:

```bash
ps aux | grep pronsole
```

### "ERROR: No ttyACM port found"

No USB printer detected. Check:

```bash
ls /dev/ttyACM*
lsusb  # List USB devices
dmesg | tail -20  # Check for connection errors
```

### "ERROR: Multiple ttyACM ports found"

Multiple printers detected. Specify which one:

```bash
pronsoled /dev/ttyACM0 115200
```

### "Permission denied" on named pipes

The daemon is running as a different user. Either:
- Run commands as the same user as the daemon
- Or run the daemon as root: `sudo pronsoled`

### Systemd service won't start

Check logs:

```bash
sudo journalctl -u pronsoled -n 50  # Last 50 lines
sudo systemctl status pronsoled
```

## Testing

Run the test suite:

```bash
bash tests/test_pronsoled.sh
```

This requires the daemon to be running and a printer connected. It will:
1. Verify the daemon creates pipes correctly
2. Test command sending
3. Test status queries
4. Test print start/abort (if test gcode available)

## Development

### Project Structure

```
pronsoled/
├── bin/
│   ├── start_pronsoled.sh         # Main daemon
│   ├── send_command.sh            # Send command to daemon
│   ├── print_status.sh            # Get printer status
│   ├── start_print.sh             # Start a print job
│   └── abort_print.sh             # Pause/abort
├── tests/
│   ├── test_pronsoled.sh          # Test suite
│   └── test_minimal.gcode         # Minimal test G-code
├── README.md
├── LICENSE (MIT)
├── install.sh
└── .gitignore
```

### Adding Features

To add a new command, create a script in `bin/` that:
1. Checks if `/tmp/pronsole/commands` exists
2. Writes a command to it
3. Reads from `/tmp/pronsole/output.log` and returns relevant lines

The `send_command.sh` script is a good template.

## License

MIT License — See LICENSE file for details.

## Contributing

Found a bug? Have a feature request? Open an issue or submit a PR!

## Credits

- Built for [Printrun/pronsole](https://github.com/kliment/Printrun) by Kliment
- Developed with the help of Claude Haiku 4.5 from Anthropic
- Whoever from Yale University, Berkeley College threw out a whole ass Ender 3
  v1 in spring move-out 2026 so I could experiment with it
