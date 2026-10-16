"""``loquor service``: run an agent at boot, restart it if it dies.

Linux: a *user* systemd unit in ``~/.config/systemd/user/`` (no root needed;
``loginctl enable-linger`` keeps it running without a login session, which
is what an unattended device wants). macOS: a launchd agent in
``~/Library/LaunchAgents/``. Both run ``python -m loquor run <profile>`` from
the venv that installed this command, so the service sees the same code
and dependencies you tested with.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loquor.protocol.discovery import slug
from loquor.settings import loquor_home


@dataclass
class Service:
    name: str
    unit_path: Path
    commands: list[str]  # what to run next, in order


def service_name(agent_name: str) -> str:
    return f"loquor-{slug(agent_name) or 'agent'}"


def install(profile: Path, agent_name: str, extra_args: list[str] | None = None, *,
            system: str | None = None, python: str | None = None, home: Path | None = None) -> Service:
    """Write the unit for ``profile``; returns it and the commands that start it."""
    system = system or platform.system()
    python = python or sys.executable
    home = home or loquor_home()
    profile = profile.resolve()
    argv = [python, "-m", "loquor", "run", str(profile), *(extra_args or [])]
    name = service_name(agent_name)
    if system == "Linux":
        unit = Path.home() / ".config" / "systemd" / "user" / f"{name}.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_systemd_unit(agent_name, argv, profile.parent, home))
        return Service(name, unit, [
            "systemctl --user daemon-reload",
            f"systemctl --user enable --now {name}",
            f"loginctl enable-linger {os.environ.get('USER', '$USER')}   # keep it running with nobody logged in",
            f"journalctl --user -u {name} -f   # the agent's log",
        ])
    if system == "Darwin":
        label = f"com.loquor.{slug(agent_name) or 'agent'}"
        unit = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        unit.parent.mkdir(parents=True, exist_ok=True)
        log_dir = home / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        unit.write_bytes(_launchd_plist(label, argv, profile.parent, home, log_dir / f"{name}.log"))
        uid = os.getuid()
        return Service(label, unit, [
            f"launchctl bootstrap gui/{uid} {shlex.quote(str(unit))}",
            f"tail -f {shlex.quote(str(log_dir / f'{name}.log'))}   # the agent's log",
        ])
    raise OSError(f"no service manager support for {system}; run `loquor run` from your own supervisor")


def uninstall(agent_name: str, *, system: str | None = None) -> Service:
    system = system or platform.system()
    name = service_name(agent_name)
    if system == "Linux":
        unit = Path.home() / ".config" / "systemd" / "user" / f"{name}.service"
        commands = [f"systemctl --user disable --now {name}", "systemctl --user daemon-reload"]
    elif system == "Darwin":
        label = f"com.loquor.{slug(agent_name) or 'agent'}"
        unit = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        commands = [f"launchctl bootout gui/{os.getuid()}/{label}"]
        name = label
    else:
        raise OSError(f"no service manager support for {system}")
    return Service(name, unit, commands)


def run_commands(commands: list[str]) -> int:
    """Run the shell commands (without their trailing comments); stop at the first failure."""
    for command in commands:
        bare = command.split("#", 1)[0].strip()
        if not bare or bare.startswith(("journalctl", "tail")):
            continue
        print(f"$ {bare}", flush=True)
        code = subprocess.call(bare, shell=True)
        if code != 0:
            return code
    return 0


def _systemd_unit(agent_name: str, argv: list[str], workdir: Path, home: Path) -> str:
    return (
        "[Unit]\n"
        f"Description=loquor agent {agent_name}\n"
        "After=network.target bluetooth.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={' '.join(shlex.quote(a) for a in argv)}\n"
        f"WorkingDirectory={workdir}\n"
        f"Environment=LOQUOR_HOME={home}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _launchd_plist(label: str, argv: list[str], workdir: Path, home: Path, log: Path) -> bytes:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": argv,
        "WorkingDirectory": str(workdir),
        "EnvironmentVariables": {"LOQUOR_HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    })
