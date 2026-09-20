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
- Python 3.10+
- Printrun (specifically `pronsole.py`)
- A user with access to the printer serial device, usually through the `dialout` group
- **Standard utilities**: `grep`, `awk`, `mkdir`, `mkfifo`, `wc`, etc. (pre-installed on Ubuntu)

## Installation

### Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/pronsoled.git
cd pronsoled
sudo ./install.sh
```

The installer will:
1. Install the Python package into the selected prefix
2. Register the `pronsoled` command in the executable path
3. Install the optional systemd unit when run as root

### User install

```bash
cd pronsoled
python3 -m pip install --user .
export PATH="$HOME/.local/bin:$PATH"
```

### Systemd Service (Optional)

```bash
sudo ./install.sh
sudo systemctl daemon-reload
sudo systemctl enable --now pronsoled
```

## Usage

### Starting the Daemon

```bash
pronsoled start                      # Auto-detect one port at 115200 baud
pronsoled start /dev/ttyACM0 115200 # Explicit port and baud rate
```

- `./config/config` — selected port, baud, PID, socket, and log paths
- `pronsoled.sock` — local command socket
- `pronsoled.log` — pronsole output

### Sending Commands

```bash
pronsoled status
pronsoled send "M105"      # Get temperature
pronsoled send "eta"       # Get ETA/status
pronsoled print /path/to/file.gcode
pronsoled abort
```

Each command returns the last few lines of the daemon's output log.

### Example Workflow

```bash
$ pronsoled start
$ pronsoled
Starting pronsole daemon
  Input:  /tmp/pronsole/commands
  Output: /tmp/pronsole/output.log
  Port:   /dev/ttyACM0 @ 115200
Printer connected successfully

$ pronsoled status
$ pronsoled-print_status
Printer is not currently printing
> 

$ pronsoled-send_command "M109 S200"
Setting nozzle temp to 200C...
$ pronsoled print my_model.gcode
$ pronsoled-start_print my_model.gcode
Printer idle, starting print...
Print job started

$ pronsoled status
$ pronsoled-print_status
Printing my_model.gcode
Estimated time: 45 minutes
```

## API/Integration

### From Shell Scripts

```bash
#!/bin/bash
if pronsoled status | grep -q "not currently printing"; then
    pronsoled print model.gcode
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

pronsoled start
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
