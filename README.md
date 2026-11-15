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

- Linux with Python 3.10+ (and `python3-venv` on Debian/Ubuntu; the system installer adds it for you)
- Printrun's `pronsole` (see [Installing Printrun](#installing-printrun))
- systemd (optional; it starts the daemon at boot)

## Installation

```bash
git clone https://github.com/silvieon/pronsoled.git
cd pronsoled
sudo ./install.sh --with-printrun
sudo systemctl enable --now pronsoled
```

`systemctl start` (and `enable --now`) waits until the daemon is connected and its socket is listening, which takes about 5 seconds. If startup fails, for example because no printer is plugged in, it reports the failure. You don't need to run `pronsoled start &` yourself. When startup fails, systemd retries every 10 seconds, so the daemon comes up on its own once you plug the printer in.

Then **log out and back in** (or run `newgrp pronsoled`) so your new group membership takes effect, and use it without `sudo`:

```bash
pronsoled status
```

The system installer:
1. Creates a virtual environment in `/opt/pronsoled/venv` and links `/usr/local/bin/pronsoled`. This avoids pip's "externally managed environment" error.
2. Creates the `pronsoled` system user and group, and adds that user to the serial group.
3. Adds the user who ran `sudo` to the `pronsoled` group.
4. Installs `/etc/systemd/system/pronsoled.service`.

To give another user access: `sudo usermod -aG pronsoled <user>`.

### Installing Printrun

There are two ways to get Printrun:

- **From your distro**, where it is packaged (Debian, Raspberry Pi OS): `sudo apt install pronsole`, then run `sudo ./install.sh` without the flag. Ubuntu 24.04 does not package it.
- **`--with-printrun`** installs Printrun from PyPI into pronsoled's own virtual environment. Printrun imports wxPython even for pronsole, and PyPI has no Linux wheels for it, so the installer uses your distro's prebuilt package instead (`python3-wxgtk4.0`, installed automatically on Debian/Ubuntu). wxPython only needs to be importable; pronsole runs fine on a headless server.

pronsoled finds pronsole on its own. It uses Printrun importable from its own environment first, which includes the distro's package. Otherwise it looks for a `pronsole.py` (pip) or `pronsole` (apt) script on `PATH`, skipping any script whose Python can't import Printrun, such as a leftover pip copy. To use a specific one, pass `--pronsole /path/to/pronsole` to `pronsoled start`.

Run `pronsoled check` to see which pronsole will be used and whether it starts. The installer runs this check as the `pronsoled` user and stops with an error if it fails.

### User install (no root at all)

```bash
./install.sh --with-printrun
systemctl --user enable --now pronsoled     # or: pronsoled start auto 115200
```

This installs into `~/.local/share/pronsoled` and `~/.local/bin`, and the socket lives in `$XDG_RUNTIME_DIR/pronsoled`. You must be in the serial group yourself (`sudo usermod -aG dialout $USER`, one time only). User services stop when you log out unless you run `loginctl enable-linger`.

### Uninstall

```bash
sudo ./install.sh --uninstall    # system install
./install.sh --uninstall         # user install
```

## Usage

```bash
pronsoled status                   # print status / ETA
pronsoled send "M105"              # nozzle & bed temperature
pronsoled send "M114"              # current position
pronsoled print /path/to/file.gcode
pronsoled abort                    # pause the current print
pronsoled check                    # which pronsole is used, and does it start?
```

`pronsoled print` copies the G-code into the daemon's runtime directory before loading it. That lets the daemon print files from your home directory, which its own user cannot read.

### Running the daemon by hand

```bash
pronsoled start /dev/ttyACM0 115200    # specific port
pronsoled start auto 115200            # auto-detect a single /dev/ttyACM* port
```

When you start it by hand, the daemon runs in the foreground as you and puts its socket in `$XDG_RUNTIME_DIR/pronsoled`. Clients look for the system service's socket first, then for that one. Use `--config-dir DIR` or `PRONSOLED_CONFIG_DIR=DIR` to point the daemon and its clients at a specific directory.

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

If pronsole dies while the daemon is running, the daemon exits, logs pronsole's exit code and last output to the journal, and systemd restarts it.

Common causes:
- pronsole cannot start (`No module named printrun` and similar). Run `pronsoled check`, then install Printrun as described in [Installing Printrun](#installing-printrun). A Printrun installed with `pip install --user` lives in that user's `~/.local` and is invisible to the service; `sudo ./install.sh --with-printrun` installs a copy the service can use.
- No printer is connected, or more than one `/dev/ttyACM*` port exists. Check with `ls /dev/ttyACM*` and set the port explicitly (see [Configuration](#configuration)).
- pronsole is not installed. Rerun `sudo ./install.sh --with-printrun`.
- The serial device belongs to a group other than `dialout`. Add the service user to it: `sudo usermod -aG <group> pronsoled`.

## Testing on a fresh Ubuntu Server

The unit tests use a fake pronsole and a virtual serial port (a pty), so they need no printer:

```bash
python3 -m unittest discover -s tests -v
```

To check the full install end to end on a clean Ubuntu Server 24.04, run the steps below as a normal user with sudo rights. Any of these works: a VM, a cloud instance, or a WSL instance imported from Ubuntu's [cloud images](https://cloud-images.ubuntu.com/wsl/releases/24.04/current/), which boot with systemd. With WSL, keep one shell open for the whole run: WSL stops an idle instance, and the fake printer goes with it. The fake printer is `tests/fake_firmware.py`, a pseudo-terminal that answers like Marlin firmware.

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/silvieon/pronsoled.git && cd pronsoled
python3 -m unittest discover -s tests -v

sudo ./install.sh --with-printrun
# Simulate a printer: a pty that answers like firmware, symlinked as the port.
sudo cp tests/fake_firmware.py /usr/local/bin/ && sudo chmod +x /usr/local/bin/fake_firmware.py
sudo systemd-run --unit=fake-printer /usr/local/bin/fake_firmware.py /dev/ttyFAKE0
sudo mkdir -p /etc/systemd/system/pronsoled.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/local/bin/pronsoled start /dev/ttyFAKE0 115200 --log-file /var/log/pronsoled/pronsoled.log\n' \
    | sudo tee /etc/systemd/system/pronsoled.service.d/test.conf
sudo systemctl daemon-reload && sudo systemctl enable --now pronsoled

# A new login shell picks up the pronsoled group; no sudo from here on.
sudo -iu "$USER" sh -c 'cd ~/pronsoled && pronsoled status && pronsoled send M105 && pronsoled print tests/test.gcode'
ls -l /run/pronsoled/          # socket is pronsoled:pronsoled, srw-rw----
```

Expected: `status` prints `Printer is not currently printing`, `M105` returns `ok T:21.0 /0.0 B:20.0 /0.0`, and `print` prints `Print job started`. A user outside the `pronsoled` group gets `permission denied ... Add yourself to the pronsoled group`.

Clean up afterwards:

```bash
sudo systemctl stop fake-printer
sudo rm -rf /etc/systemd/system/pronsoled.service.d /usr/local/bin/fake_firmware.py
sudo ./install.sh --uninstall
sudo gpasswd -d "$USER" pronsoled
sudo userdel pronsoled && sudo rm -rf /var/lib/pronsoled /var/log/pronsoled
sudo apt autoremove --purge -y python3-wxgtk4.0    # if it was only installed for this test
cd .. && rm -rf pronsoled
```

## Project Structure

```
pronsoled/
├── pronsoled/
│   ├── cli.py           # command-line interface
│   ├── daemon.py        # socket server & pronsole wrapper
│   └── __init__.py
├── tests/               # unit tests with fake pronsole / fake serial port
├── pyproject.toml
├── pronsoled.service    # systemd unit (runs as the pronsoled user)
├── install.sh
├── README.md
└── LICENSE (MIT)
```

## Version History

### v0.1.5
- `--with-printrun` now always installs Printrun into the venv. Before, a `pip install --user printrun` in `~/.local` counted as "already installed", but the service user can't see it, which caused `No module named printrun`
- `pronsoled check` shows where Printrun is imported from

### v0.1.4
- Finds apt-installed Printrun (`/usr/bin/pronsole`, or the distro's Python package), and skips stale `pronsole.py` scripts that can't import Printrun
- New `pronsoled check`; the installer runs it as the service user and fails loudly if pronsole can't start
- If pronsole dies, the daemon exits with its exit code and last output in the journal, so systemd restarts it, instead of answering "pronsole process is not running" forever

### v0.1.3
- systemd `Type=notify`: `systemctl start` waits until the socket is ready and reports startup failures
- `systemctl stop` exits cleanly instead of leaving the unit `failed`
- Startup fails (and systemd retries) when pronsole cannot connect to the printer, instead of running offline
- Clients find the system socket even while the service is restarting, and point to `journalctl` when it is down

### v0.1.2
- The service runs as an unprivileged `pronsoled` user; the CLI no longer needs `sudo`
- The socket lives at `/run/pronsoled/pronsoled.sock`, mode 0660, group `pronsoled`
- The installer uses a dedicated venv (no more PEP 668 / `--break-system-packages`) and adds `--with-printrun` and `--uninstall`
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
