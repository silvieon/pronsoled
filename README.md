# pronsoled

A stable daemon wrapper for [pronsole](https://github.com/kliment/Printrun) (Printrun's command-line interface) that solves the multi-instance serial contention issue on 3D printers.

## The Problem

Running multiple instances of pronsole against the same serial port causes intermittent print pauses and serial communication glitches. This happens because each instance competes for exclusive access to the USB serial device, leading to write collisions and dropped packets.

**pronsoled** solves this by keeping a **single persistent pronsole instance** and routing every command through it over a Unix socket. This gives you:
- **One connection per printer**, so there is no serial contention
- **Stable printing**, with no unexpected pauses or glitches
- **Multiple clients**: different tools and scripts can send commands at the same time without interfering

## How It Works

```
Client 1 ──┐
           ├──> /run/pronsoled/pronsoled.sock ──> pronsole ──> /dev/ttyACM0
Client 2 ──┤    (group "pronsoled", mode 0660)    (one, persistent)
Client N ──┘
```

The systemd service runs as an unprivileged `pronsoled` system user that belongs to the serial group (`dialout`, or `uucp` on Arch). Members of the `pronsoled` group can use the socket, so **day-to-day commands need no `sudo`**.

## Requirements

- Linux with systemd and Python 3.10+
- Internet access while installing

Printrun is installed for you. No compiler, wxPython or desktop libraries are needed.

## Installation

```bash
git clone https://github.com/silvieon/pronsoled.git
cd pronsoled
sudo ./install.sh
sudo systemctl enable --now pronsoled
```

`systemctl start` (and `enable --now`) waits until the daemon is connected and its socket is listening, which takes about 5 seconds. If startup fails, for example because no printer is plugged in, it reports the failure, and systemd retries every 10 seconds. The daemon comes up on its own once you plug the printer in.

Then **log out and back in** so your new group membership takes effect, and use it without `sudo`:

```bash
pronsoled status
```

The installer:
1. Creates an isolated virtual environment in `/opt/pronsoled/venv` and links `/usr/local/bin/pronsoled`. On Debian and Ubuntu it installs `python3-venv` first if it is missing.
2. Adds Printrun 2.2.0 to that environment (see below), plus `pyserial` and `platformdirs`.
3. Creates the `pronsoled` system user and group, and adds that user to the serial group.
4. Installs `/etc/systemd/system/pronsoled.service` and checks that pronsole starts as the `pronsoled` user.
5. Adds the user who ran `sudo` to the `pronsoled` group.

To upgrade, run the installer again and then `sudo systemctl restart pronsoled`. It rebuilds the environment from scratch, so don't upgrade during a print.

To give another user access: `sudo usermod -aG pronsoled <user>`.
To remove pronsoled: `sudo ./install.sh --uninstall`.

### How Printrun is installed

The installer downloads Printrun's source release from PyPI, verifies its SHA-256 checksum, and copies the `printrun` package into the environment as plain Python. It does not run `pip install printrun`, for two reasons:

- PyPI has no prebuilt Printrun for newer Pythons (such as 3.14 on Ubuntu 26.04), so pip would need a C compiler to build Printrun's optional G-code extension. Printrun works without that extension.
- Printrun's requirements include wxPython, which pronsole imports but never uses. `pronsoled/_pronsole.py` starts pronsole with empty stand-ins for it.

As a result, pronsole logs three harmless warnings each time it starts, about the GCoder implementation, DBus and psutil.

To run a different pronsole, pass `--pronsole /path/to/pronsole.py` to `pronsoled start`.

## Usage

```bash
pronsoled status                   # print status / ETA
pronsoled send "M105"              # nozzle & bed temperature
pronsoled send "M114"              # current position
pronsoled print /path/to/file.gcode
pronsoled abort                    # pause the current print
```

`pronsoled print` copies the G-code into the daemon's runtime directory before loading it. That lets the daemon print files from your home directory, which its own user cannot read.

### Running the daemon by hand

```bash
pronsoled start /dev/ttyACM0 115200    # specific port
pronsoled start auto 115200            # auto-detect a single /dev/ttyACM* port
```

Started this way, the daemon runs in the foreground as you, so you need access to the serial port yourself (`sudo usermod -aG dialout $USER`). Its socket goes in `$XDG_RUNTIME_DIR/pronsoled`. Clients use the systemd service when it is running, and otherwise a daemon started by hand. Use `--config-dir DIR` or `PRONSOLED_CONFIG_DIR=DIR` to point the daemon and its clients at a specific directory.

## Configuration

To change the port or baud rate, override `ExecStart` and restart:

```bash
sudo systemctl edit pronsoled
```

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/pronsoled start /dev/ttyUSB0 250000 --log-file /var/log/pronsoled/pronsoled.log
```

```bash
sudo systemctl restart pronsoled
```

| Path | Contents |
| --- | --- |
| `/run/pronsoled/` | socket, PID, setup record (`config`), staged print job |
| `/var/log/pronsoled/pronsoled.log` | raw pronsole output |
| `/var/lib/pronsoled/` | the service user's home (Printrun settings) |

## Integration

### From shell scripts

```bash
#!/bin/sh
if pronsoled status | grep -q "not currently printing"; then
    pronsoled print model.gcode
else
    echo "Printer busy"
fi
```

### From Python

```python
import subprocess

def send_command(cmd: str) -> str:
    result = subprocess.run(["pronsoled", "send", cmd], capture_output=True, text=True, check=True)
    return result.stdout.strip()

print(send_command("eta"))
```

## Troubleshooting

**`permission denied on /run/pronsoled/pronsoled.sock`**: you are not in the `pronsoled` group yet, or you haven't logged in again since being added. Check with `id`. Fix it with `sudo usermod -aG pronsoled $USER`, then log out and back in.

**`daemon is not running`**: the service failed to start or is still retrying. Check it:

```bash
systemctl status pronsoled
journalctl -u pronsoled -n 50
```

Common causes:
- No printer is connected, or more than one `/dev/ttyACM*` port exists. Check with `ls /dev/ttyACM*` and set the port explicitly (see [Configuration](#configuration)).
- The serial device belongs to a group other than `dialout`. Add the service user to it: `sudo usermod -aG <group> pronsoled`.
- pronsole itself fails. If pronsole exits, the daemon exits too and writes pronsole's exit code and last output to the journal, and systemd restarts it. Re-running `sudo ./install.sh` rebuilds the environment.

## Testing on a fresh Ubuntu Server

The unit tests use a fake pronsole and a virtual serial port (a pty), so they need no printer:

```bash
python3 -m unittest discover -s tests -v
```

To check the full install end to end on a clean Ubuntu Server, run the steps below as a normal user with sudo rights. Any of these works: a VM, a cloud instance, or a WSL instance imported from Ubuntu's [cloud images](https://cloud-images.ubuntu.com/) with systemd enabled. With WSL, keep one shell open for the whole run: WSL stops an idle instance, and the fake printer goes with it. The fake printer is `tests/fake_firmware.py`, a pseudo-terminal that answers like Marlin firmware.

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/silvieon/pronsoled.git && cd pronsoled
python3 -m unittest discover -s tests -v

sudo ./install.sh
# Simulate a printer: a pty that answers like firmware, symlinked as the port.
sudo cp tests/fake_firmware.py /usr/local/bin/ && sudo chmod +x /usr/local/bin/fake_firmware.py
sudo systemd-run --unit=fake-printer /usr/local/bin/fake_firmware.py /dev/ttyFAKE0
sudo mkdir -p /etc/systemd/system/pronsoled.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/local/bin/pronsoled start /dev/ttyFAKE0 115200 --log-file /var/log/pronsoled/pronsoled.log\n' \
    | sudo tee /etc/systemd/system/pronsoled.service.d/test.conf
sudo systemctl daemon-reload && sudo systemctl enable --now pronsoled

# A new login shell picks up the pronsoled group; no sudo from here on.
sudo -iu "$USER" sh -c 'cd ~/pronsoled && pronsoled status && pronsoled send M105 && pronsoled print tests/test.gcode'
sudo ls -l /run/pronsoled/     # socket is pronsoled:pronsoled, srw-rw----
```

Expected: `status` prints `Printer is not currently printing`, `M105` returns `ok T:21.0 /0.0 B:20.0 /0.0`, and `print` prints `Print job started`. A user outside the `pronsoled` group gets `permission denied ... Add yourself to the pronsoled group`.

Clean up afterwards:

```bash
sudo systemctl stop fake-printer
sudo rm -rf /etc/systemd/system/pronsoled.service.d /usr/local/bin/fake_firmware.py
sudo ./install.sh --uninstall
sudo gpasswd -d "$USER" pronsoled
sudo userdel pronsoled && sudo rm -rf /var/lib/pronsoled /var/log/pronsoled
cd .. && rm -rf pronsoled
```

Tested this way on Ubuntu Server 26.04 (Python 3.14) and 22.04 (Python 3.10).

## Project Structure

```
pronsoled/
├── pronsoled/
│   ├── cli.py           # command-line interface
│   ├── daemon.py        # socket server & pronsole wrapper
│   ├── _pronsole.py     # starts Printrun's pronsole without wxPython
│   └── __init__.py
├── tests/               # unit tests, fake pronsole, fake printer
├── pyproject.toml
├── pronsoled.service    # systemd unit (runs as the pronsoled user)
├── install.sh
├── README.md
└── LICENSE (MIT)
```

## Version History

### v0.1.6
- The installer now works on a stock Ubuntu 26.04 (Python 3.14): isolated environment, Printrun installed as plain Python with a pinned checksum, no compiler or wxPython needed
- Printrun is always installed; `--with-printrun`, `pronsoled check`, the search for pronsole on `PATH` and the no-root user install are gone

### v0.1.2 – v0.1.5
- The service runs as an unprivileged `pronsoled` user; the CLI no longer needs `sudo`
- The socket lives at `/run/pronsoled/pronsoled.sock`, mode 0660, group `pronsoled`
- `systemctl start` waits until the socket is ready and reports startup failures; `systemctl stop` exits cleanly
- Startup fails, and systemd retries, when pronsole cannot connect to the printer
- If pronsole dies, the daemon exits with pronsole's exit code and last output in the journal, and systemd restarts it
- Print jobs are staged so the daemon can read files from private home directories
- Clearer errors when the daemon is not running or you lack permission

### v0.1.1
- Rewrite from bash to Python
- Single Unix socket instead of named pipes
- systemd service integration

### v0.1.0
- Initial bash-based implementation

## License

MIT License. See the LICENSE file for details.

## Credits

- Built for [Printrun/pronsole](https://github.com/kliment/Printrun) by Kliment Yanev
- Developed with help from Claude (Anthropic)
