"""install.sh, the systemd unit and the README, as far as they can be checked without installing.

tests/system_test.sh covers the installation itself.
"""
from __future__ import annotations

import contextlib
import io
import re
import shlex
import shutil
import subprocess
import unittest

from support import ROOT, not_as_root

import pronsoled
from pronsoled import cli
from pronsoled.daemon import SOCKET

INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
UNIT = (ROOT / "pronsoled.service").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SHELL_OPERATORS = ("|", "||", "&&", ";", "then", "do")


def unit_settings() -> dict[str, list[str]]:
    settings: dict[str, list[str]] = {}
    for line in UNIT.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings.setdefault(key, []).append(value)
    return settings


def install_variable(name: str) -> str:
    return re.search(rf"^{name}=(.+)$", INSTALL, re.MULTILINE).group(1)


def readme_code() -> str:
    """The README's code blocks and inline code, one piece per line."""
    fenced = r"^```\w*\n(.*?)^```"
    blocks = re.findall(fenced, README, re.MULTILINE | re.DOTALL)
    spans = re.findall(r"`([^`\n]+)`", re.sub(fenced, "", README, flags=re.MULTILINE | re.DOTALL))
    return "\n".join(blocks + spans)


def command_lines(text: str) -> list[list[str]]:
    """The arguments of every `pronsoled ...` command in lines of shell or unit-file text."""
    found = []
    for line in text.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            in_command_position = index == 0 or tokens[index - 1] in (*SHELL_OPERATORS, "if", "sudo")
            if (token == "pronsoled" and in_command_position) or token.endswith("/bin/pronsoled"):
                rest = tokens[index + 1:]
                end = next((i for i, word in enumerate(rest) if word in SHELL_OPERATORS or word.endswith(";")), len(rest))
                if rest[:end]:  # a bare `pronsoled` is the name of the user, the group or the service
                    found.append(rest[:end])
    return found


def accepted(arguments: list[str]) -> bool:
    if arguments in (["start"], ["status"], ["send"], ["print"], ["abort"]):
        return True  # the text names a command without showing a full command line
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.parser().parse_args(arguments)
        except SystemExit as exit_:
            return exit_.code == 0
    return True


class InstallScriptTest(unittest.TestCase):
    def test_shell_syntax(self):
        for script in ("install.sh", "tests/system_test.sh"):
            interpreter = (ROOT / script).read_text(encoding="utf-8").splitlines()[0].removeprefix("#!")
            result = subprocess.run([interpreter, "-n", str(ROOT / script)], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr), (0, ""), script)

    @not_as_root
    def test_refuses_to_run_without_root(self):
        for arguments in ([], ["--uninstall"]):
            result = subprocess.run(["sh", str(ROOT / "install.sh"), *arguments], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertIn("Run as root", result.stderr)

    def test_printrun_is_pinned_by_version_and_checksum(self):
        self.assertRegex(install_variable("PRINTRUN"), r"^printrun-\d+\.\d+\.\d+$")
        self.assertRegex(install_variable("PRINTRUN_SHA256"), r"^[0-9a-f]{64}$")
        self.assertIn("sha256sum --check", INSTALL)

    def test_installed_command_ignores_the_working_directory_and_python_environment(self):
        """`python -m` alone would import a pronsoled/ or json.py found in the current directory."""
        self.assertIn('-I -m pronsoled.cli "$@"', INSTALL)


class UnitFileTest(unittest.TestCase):
    def setUp(self):
        self.unit = unit_settings()

    def test_runs_as_the_unprivileged_user_the_installer_creates(self):
        self.assertEqual(self.unit["User"], ["pronsoled"])
        self.assertIn("useradd --system --gid pronsoled", INSTALL)

    def test_socket_directory_is_where_clients_look_and_open_to_the_group(self):
        self.assertEqual(self.unit["RuntimeDirectory"], ["pronsoled"])
        self.assertEqual(self.unit["RuntimeDirectoryMode"], ["2770"])  # setgid: staged jobs get the group
        self.assertEqual(self.unit["Environment"], [f"PRONSOLED_CONFIG_DIR={cli.SYSTEM_DIR}"])
        self.assertEqual(str(cli.SYSTEM_DIR), "/run/pronsoled")

    def test_exec_start_is_a_command_the_cli_accepts(self):
        executable, *arguments = shlex.split(self.unit["ExecStart"][0])
        self.assertEqual(executable, install_variable("BIN"))
        self.assertEqual(cli.parser().parse_args(arguments).action, "start")

    def test_start_waits_for_readiness_and_failures_are_retried(self):
        self.assertEqual(self.unit["Type"], ["notify"])
        self.assertEqual(self.unit["Restart"], ["on-failure"])
        self.assertEqual(self.unit["WantedBy"], ["multi-user.target"])

    def test_stop_signals_only_the_daemon(self):
        """With the default KillMode, pronsole would get SIGTERM too and look like a crash."""
        self.assertEqual(self.unit["KillMode"], ["mixed"])


class ReadmeTest(unittest.TestCase):
    def test_every_pronsoled_command_shown_is_valid(self):
        commands = command_lines(readme_code())
        self.assertGreater(len(commands), 8, "the README should show the commands")
        for arguments in commands:
            with self.subTest(command="pronsoled " + " ".join(arguments)):
                self.assertTrue(accepted(arguments))

    def test_the_check_above_does_catch_invalid_commands(self):
        sample = "pronsoled status\nif pronsoled bogus | grep x; then\nExecStart=/usr/local/bin/pronsoled start --log-file x\n"
        self.assertEqual([accepted(arguments) for arguments in command_lines(sample)], [True, False, False])

    def test_current_version_heads_the_version_history(self):
        self.assertEqual(re.search(r"^### v(\S+)", README, re.MULTILINE).group(1), pronsoled.__version__)

    def test_names_the_real_paths(self):
        for path in (f"{cli.SYSTEM_DIR}/{SOCKET}", install_variable("VENV"), install_variable("BIN"),
                     install_variable("UNIT"), "journalctl -u pronsoled", "/var/lib/pronsoled/printrun.log"):
            self.assertTrue(path in README, f"the README does not mention {path}")

    def test_documents_every_command(self):
        for command in ("start", "status", "send", "print", "abort"):
            self.assertRegex(README, rf"pronsoled {command}\b")


class RepositoryTest(unittest.TestCase):
    def tracked(self) -> list[str]:
        if not (ROOT / ".git").exists() or not shutil.which("git"):
            self.skipTest("not a git checkout")
        return subprocess.run(["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()

    def test_scripts_are_executable(self):
        modes = {line.split("\t")[1]: line.split()[0] for line in self.tracked()}
        for script in ("install.sh", "tests/system_test.sh", "tests/fake_firmware.py"):
            self.assertEqual(modes[script], "100755", script)

    def test_files_have_unix_line_endings(self):
        """A CRLF in install.sh or the unit file breaks them on Linux."""
        for line in self.tracked():
            path = line.split("\t")[1]
            self.assertNotIn(b"\r\n", (ROOT / path).read_bytes(), path)


if __name__ == "__main__":
    unittest.main()
