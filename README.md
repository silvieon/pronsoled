# pronsoled

A daemon that keeps one [pronsole](https://github.com/kliment/Printrun) (Printrun's command-line interface) connected to your 3D printer and lets any number of tools and scripts send commands through it.

Running several pronsole instances against the same serial port makes them fight over it: writes collide, packets are dropped and prints pause. With pronsoled there is one connection per printer, and everything else talks to the daemon.

```
Client 1 ──┐
           ├──> /run/pronsoled/pronsoled.sock ──> pronsole ──> /dev/ttyACM0
Client 2 ──┤    (group "pronsoled", mode 0660)    (one, persistent)
Client N ──┘
```

The service runs as an unprivileged `pronsoled` user. Members of the `pronsoled` group can use the socket, so everyday commands need no `sudo`.

## Requirements

- Linux with systemd and Python 3.10 or newer
- Internet access while installing

Printrun is installed for you. No compiler, wxPython or desktop libraries are needed.

## Install

```bash
git clone https://github.com/silvieon/pronsoled.git
cd pronsoled
sudo ./install.sh
```

Log out and back in, so that your new group membership takes effect. Then:

```bash
pronsoled status
```

The installer:

1. Builds an isolated Python environment in `/opt/pronsoled/venv` with pronsoled, Printrun 2.2.0, `pyserial` and `platformdirs`. On Debian and Ubuntu it installs `python3-venv` first if that is missing.
2. Creates the command `/usr/local/bin/pronsoled`.
3. Creates the `pronsoled` user and group, puts that user in the serial group (`dialout`, or `uucp` on Arch), and puts you in the `pronsoled` group.
4. Checks that pronsole starts as the `pronsoled` user.
5. Installs `/etc/systemd/system/pronsoled.service`, enables it at boot and starts it.

If no printer is connected, the last step says so. That is fine: the service retries every 10 seconds and comes up when the printer does.

| To | Run |
| --- | --- |
| Upgrade | `git pull && sudo ./install.sh`. This restarts the daemon, which ends a running print. |
| Give another user access | `sudo usermod -aG pronsoled <user>`, after which they log out and back in |
| Uninstall | `sudo ./install.sh --uninstall`. This removes everything listed above, including the `pronsoled` user and its Printrun settings. A port override you made with `systemctl edit` stays. |

## Usage

```bash
pronsoled status                     # progress of the current print
pronsoled send M105                  # any G-code: here, the temperatures
pronsoled send "settemp 200"         # or any pronsole command
pronsoled print /path/to/file.gcode
pronsoled abort                      # pause the print; `pronsoled send resume` continues it
```

Each command prints what pronsole answered and exits with 0. On an error it prints one line starting with `pronsoled: error:` and exits with 1.

- `print` refuses to start while a print is running. It copies the file next to the socket first, because the service cannot read your home directory.
- Large files take a moment to load, about 2 seconds per 10 MB. `print` returns at once and the print starts when loading is done.
- A reply is whatever pronsole prints until it has been quiet for a tenth of a second. An answer that takes the printer longer, such as the `ok` after homing, appears only in the [log](#logs).

## The service

```bash
sudo systemctl stop pronsoled        # also: start, restart
systemctl status pronsoled
```

- `systemctl start` returns when the printer is online and `pronsoled status` works. It waits up to 30 seconds for the printer to answer.
- If the printer is missing, unplugged later or switched off, or if pronsole dies, the daemon exits with the reason in the log. systemd starts it again every 10 seconds until it works, so the service recovers when the printer is back.
- Stopping or restarting the service ends a running print.

By default the service uses the only `/dev/ttyACM*` port at 115200 baud. To use another port or baud rate:

```bash
sudo systemctl edit pronsoled
```

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/pronsoled start /dev/ttyUSB0 250000
```

```bash
sudo systemctl restart pronsoled
```

## Logs

| What | Where | How to read it |
| --- | --- | --- |
| The service: the daemon's own messages and everything pronsole prints | the systemd journal | `journalctl -u pronsoled` (add `-f` to follow, `-n 50` for the last lines, `-b` for this boot) |
| Printrun's own log: the same pronsole lines with timestamps | `/var/lib/pronsoled/printrun.log` | `sudo tail -n 50 /var/lib/pronsoled/printrun.log` |
| A daemon started by hand | its terminal, and `~/printrun.log` | |
| `install.sh` and the client commands | the terminal only | |

Printrun never trims `printrun.log`. To empty it: `sudo truncate -s 0 /var/lib/pronsoled/printrun.log`.

Other files the service writes:

| Path | Contents |
| --- | --- |
| `/run/pronsoled/` | the socket and the staged print job (`job.gcode`). It exists only while the service runs. |
| `/var/lib/pronsoled/.config/Printrun/pronsolerc` | Printrun's settings |
| `/var/lib/pronsoled/.cache/Printrun/` | Printrun's backup of those settings, and its command history |

A healthy start looks like this in the journal. The warnings are expected: they are about optional parts of Printrun that pronsoled does not install. The `stty` lines appear on Ubuntu 26.04 and are harmless too.

```
Starting pronsoled for /dev/ttyACM0 @ 115200
WARNING:root:Memory-efficient GCoder implementation unavailable: ...
WARNING:root:Could not setup DBus for sleep inhibition: No module named 'dbus'
WARNING:root:psutil unavailable, could not import power utils:No module named 'psutil'
Welcome to the printer console! Type "help" for a list of available commands.
stty: invalid argument '-hup'
Try 'stty --help' for more information.
Printer is now online
Ready: listening on /run/pronsoled/pronsoled.sock
```

## Running the daemon by hand

```bash
pronsoled start                      # the only /dev/ttyACM* port, 115200 baud
pronsoled start /dev/ttyUSB0 250000
```

The daemon then runs in the foreground as you until you press Ctrl-C, so you need access to the serial port yourself (`sudo usermod -aG dialout $USER`). Stop the service first: two daemons must not share a printer.

Its socket goes in `$XDG_RUNTIME_DIR/pronsoled`. Client commands use the service while it runs and a hand-started daemon otherwise. To put the socket somewhere else, give the daemon and the clients the same `--config-dir DIR` option or `PRONSOLED_CONFIG_DIR` variable.

## Use from scripts

```bash
#!/bin/sh
if pronsoled status | grep -q "not currently printing"; then
    pronsoled print model.gcode
else
    echo "Printer busy"
fi
```

```python
import subprocess

def send_command(command: str) -> str:
    result = subprocess.run(["pronsoled", "send", command], capture_output=True, text=True, check=True)
    return result.stdout.strip()

print(send_command("M105"))
```

## Troubleshooting

**`permission denied on /run/pronsoled/pronsoled.sock`**: you are not in the `pronsoled` group, or have not logged in again since you were added. `id` shows your groups. Fix it with `sudo usermod -aG pronsoled $USER`, then log out and back in.

**`daemon is not running`**: the service is stopped, or failing and retrying. `journalctl -u pronsoled -n 20` shows the reason:

| The journal says | Cause and fix |
| --- | --- |
| `expected exactly one /dev/ttyACM* port but found none` | The printer is not connected or is switched off. If its port has another name, such as `/dev/ttyUSB0`, [set the port](#the-service). |
| `expected exactly one /dev/ttyACM* port but found ...` and several ports | [Set the port](#the-service). |
| `Connection error: Could not connect to serial port` | The port cannot be opened. If it exists, the service user probably lacks access: look at the port's group with `ls -l` and add the service user to it with `sudo usermod -aG <group> pronsoled`. |
| `did not come online within 30 seconds` | The port opened but nothing answered: wrong port, wrong baud rate, or the printer is still starting. |
| `lost the printer` | The printer was unplugged or switched off. The service reconnects by itself when it is back. |
| `pronsole exited unexpectedly` | The lines above it are pronsole's own error. If it happens at every start, run `sudo ./install.sh` again to rebuild the environment. |
| `unrecognized arguments: --log-file` | An override from an older version. Remove that option with `sudo systemctl edit pronsoled`. |

## Security

Anyone in the `pronsoled` group has full control of the printer. pronsole can also run Python and shell commands (`!`, `run_script`), so members of the group can run programs as the `pronsoled` user. Add only people you trust with both.

## How Printrun is installed

The installer downloads Printrun's source release from PyPI, verifies its SHA-256 checksum, and copies the `printrun` package into the environment as plain Python. It does not run `pip install printrun`, for two reasons:

- PyPI has no prebuilt Printrun for newer Pythons, such as 3.14 on Ubuntu 26.04, so pip would need a C compiler for Printrun's optional G-code extension. Printrun works without that extension.
- Printrun's requirements include wxPython, which pronsole imports but never uses. `pronsoled/_pronsole.py` starts pronsole with empty stand-ins for it, and without its prompt.

## Testing

```bash
python3 -m unittest discover -s tests
```

This needs no printer and no Printrun: the tests run the daemon against a fake Printrun (`tests/fakes`). The tests in `tests/test_printrun.py` use the real Printrun and a fake printer on a pseudo-terminal (`tests/fake_firmware.py`). They are skipped unless Printrun is installed for the Python you use, which it is after an install:

```bash
/opt/pronsoled/venv/bin/python -m unittest discover -s tests
```

`tests/system_test.sh` tests the installation itself, end to end:

```bash
sudo tests/system_test.sh
```

**Run it only on a throwaway machine**: a VM, a cloud instance, or a WSL instance with systemd enabled. It installs pronsoled, creates test users, runs both sets of tests above, drives the service with a fake printer, and removes everything again. It checks, among other things, that group members need no `sudo`, that other users are refused, that the service stops cleanly and recovers from a killed pronsole and an unplugged printer, and that an uninstall leaves nothing behind. It refuses to run where pronsoled is already installed.

Tested this way on Ubuntu Server 26.04 (Python 3.14) and 22.04 (Python 3.10).

## Project structure

```
pronsoled/
├── pronsoled/
│   ├── cli.py            # command line
│   ├── daemon.py         # the daemon, and the client's call to it
│   └── _pronsole.py      # starts Printrun's pronsole without wxPython
├── tests/
│   ├── test_*.py         # unit tests
│   ├── fakes/            # a fake Printrun
│   ├── fake_firmware.py  # a fake printer
│   └── system_test.sh    # end-to-end test for a throwaway machine
├── pronsoled.service     # systemd unit
├── install.sh
└── LICENSE (MIT)
```

## Version history

### v0.2.0
- Simpler throughout: about a quarter less code, with five times as much test code
- `sudo ./install.sh` now also enables and starts the service
- The daemon is ready as soon as the printer is online, instead of after a fixed 5 seconds of self-tests
- The service recovers by itself after the printer is unplugged or switched off
- Replies no longer contain pronsole's prompt and colour codes
- pronsoled no longer writes a log file of its own: everything goes to the systemd journal. `/var/log/pronsoled/` can be deleted
- `--uninstall` removes the `pronsoled` user and group as well
- Removed: the `--log-file` and `--pronsole` options, the PID file and `config` record in the socket directory, `install.sh --with-printrun`, and `pyproject.toml`

### v0.1.6
- The installer works on a stock Ubuntu 26.04 (Python 3.14): isolated environment, Printrun installed as plain Python with a pinned checksum, no compiler or wxPython needed

### v0.1.2 – v0.1.5
- The service runs as an unprivileged `pronsoled` user; the CLI no longer needs `sudo`
- The socket lives at `/run/pronsoled/pronsoled.sock`, mode 0660, group `pronsoled`
- `systemctl start` waits until the socket is ready and reports startup failures; `systemctl stop` exits cleanly
- If pronsole dies, the daemon exits and systemd restarts it
- Print jobs are staged so the daemon can read files from private home directories

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
