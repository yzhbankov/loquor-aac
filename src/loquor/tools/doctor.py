"""``loquor doctor``: is this device ready to run an agent?

Every check prints ok / warn / FAIL and, when something is wrong, the one
command that fixes it. It covers the things the first run on a new machine
trips over: Python, the package, Ollama and the model, the transport's
prerequisites (a LAN address for Wi-Fi; the radio libraries, a powered
adapter and LE mode for Bluetooth), and the state directory.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loquor.settings import Settings, config_path, loquor_home

OK, WARN, FAIL = "ok", "warn", "FAIL"
MODEL_RAM_GB = {"0.5b": 1.5, "1.5b": 3, "3b": 6, "7b": 10, "8b": 12}  # rough minimum per model size


@dataclass
class Check:
    name: str
    state: str  # ok | warn | FAIL
    detail: str = ""
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.state != FAIL


async def run_checks(settings: Settings, *, transport: str | None = None, llm: bool = True) -> list[Check]:
    checks = [check_python(), check_package(), *check_dependencies(), check_home(), check_config()]
    if llm:
        checks += await check_ollama(settings)
    checks += check_transport(transport or settings.transport)
    return checks


def check_python() -> Check:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} at {sys.executable}"
    if v < (3, 10):
        return Check("python", FAIL, detail, "install Python 3.10 or newer and recreate the venv")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return Check("python", OK, detail + ("" if in_venv else " (no virtualenv active)"))


def check_package() -> Check:
    try:
        import loquor

        return Check("loquor", OK, f"{loquor.__version__} from {Path(loquor.__file__).parent}")
    except Exception as exc:  # pragma: no cover - cannot happen when running loquor itself
        return Check("loquor", FAIL, str(exc), 'pip install -e "."')


def check_dependencies() -> list[Check]:
    out = []
    for module, hint in (("aiohttp", ""), ("cbor2", ""), ("jsonschema", ""), ("nacl", "pynacl")):
        try:
            importlib.import_module(module)
        except ImportError:
            out.append(Check(f"dependency {hint or module}", FAIL, "not installed", 'pip install -e "."'))
    return out or [Check("dependencies", OK, "aiohttp, cbor2, jsonschema, pynacl")]


def check_home() -> Check:
    home = loquor_home()
    try:
        (home / "keys").mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        return Check("state directory", FAIL, f"{home}: {exc}", f"make {home} writable or set LOQUOR_HOME")
    keys = sorted(p.name for p in (home / "keys").glob("*.key"))
    return Check("state directory", OK, f"{home} ({len(keys)} identity key{'s' if len(keys) != 1 else ''})")


def check_config() -> Check:
    path = config_path()
    if not path.exists():
        return Check("device config", WARN, f"{path} not found; defaults in use (transport localhost, model qwen2.5:3b)",
                     "loquor config set transport wifi   # or run ./install.sh")
    try:
        s = Settings.load(path)
    except ValueError as exc:
        return Check("device config", FAIL, str(exc), f"fix or delete {path}")
    return Check("device config", OK, f"transport {s.transport}, model {s.model}, beacons every {s.beacon_interval:g} s")


async def check_ollama(settings: Settings) -> list[Check]:
    from loquor.reasoning.ollama import ollama_available  # noqa: PLC0415

    if not await ollama_available(settings.ollama_url):
        fix = "brew install ollama && brew services start ollama" if platform.system() == "Darwin" \
            else "curl -fsSL https://ollama.com/install.sh | sh"
        return [Check("ollama", FAIL, f"not reachable at {settings.ollama_url}", f"{fix}   # or: ollama serve")]
    checks = [Check("ollama", OK, f"reachable at {settings.ollama_url}")]
    if await ollama_available(settings.ollama_url, model=settings.model):
        checks.append(Check(f"model {settings.model}", OK, "pulled"))
    else:
        checks.append(Check(f"model {settings.model}", FAIL, "not pulled", f"ollama pull {settings.model}"))
    ram = ram_gb()
    need = model_ram_gb(settings.model)
    if ram and need and ram < need:
        checks.append(Check("memory", WARN, f"{ram:.0f} GB RAM; {settings.model} wants about {need:g} GB",
                            "loquor config set model qwen2.5:1.5b   # or qwen2.5:0.5b on small boards"))
    return checks


def check_transport(name: str) -> list[Check]:
    if name == "localhost":
        return [Check("transport localhost", OK, "agents on this machine only; nothing to set up")]
    if name == "wifi":
        from loquor.transports.multicast import primary_ip  # noqa: PLC0415

        ip = primary_ip()
        if ip.startswith("127."):
            return [Check("transport wifi", FAIL, "no LAN address (not on any network)",
                          "join a Wi-Fi network or hotspot, or: loquor config set interface <local-ip>")]
        return [Check("transport wifi", OK, f"LAN address {ip} (multicast; guest/corporate Wi-Fi may block it)")]
    if name == "bluetooth":
        return check_bluetooth()
    return [Check(f"transport {name}", WARN, "custom transport; nothing to check")]


def check_bluetooth() -> list[Check]:
    checks = []
    missing = [m for m in ("bleak", "bless") if importlib.util.find_spec(m) is None]
    if missing:
        return [Check("transport bluetooth", FAIL, f"radio libraries missing: {', '.join(missing)}",
                      'pip install -e ".[ble]"')]
    checks.append(Check("bluetooth libraries", OK, "bleak and bless installed"))
    system = platform.system()
    if system == "Linux":
        if shutil.which("bluetoothctl") is None:
            checks.append(Check("bluetooth adapter", FAIL, "bluetoothctl not found", "sudo apt install bluez"))
            return checks
        show = _run(["bluetoothctl", "show"])
        if "Powered: yes" in show:
            checks.append(Check("bluetooth adapter", OK, "powered"))
        elif "No default controller" in show or not show.strip():
            checks.append(Check("bluetooth adapter", FAIL, "no adapter found", "sudo systemctl start bluetooth"))
        else:
            checks.append(Check("bluetooth adapter", FAIL, "adapter off", "bluetoothctl power on"))
        conf = Path("/etc/bluetooth/main.conf")
        text = conf.read_text() if conf.exists() else ""
        if any(line.strip().lower().replace(" ", "") == "controllermode=le" for line in text.splitlines()):
            checks.append(Check("bluetooth LE mode", OK, f"ControllerMode = le in {conf}"))
        else:
            checks.append(Check("bluetooth LE mode", WARN, f"ControllerMode = le not set in {conf} "
                                "(dual-mode laptops then try classic connections and fail)",
                                "sudo ./install.sh --ble-le-mode   # or edit main.conf and: sudo systemctl restart bluetooth"))
    elif system == "Darwin":
        checks.append(Check("bluetooth permission", WARN, "cannot be verified from here",
                            "allow Bluetooth for your terminal in System Settings → Privacy & Security → Bluetooth when asked"))
    checks.append(Check("bluetooth pairing", WARN, "loquor never pairs; a stale pairing between two devices breaks connections",
                        "forget the other device in the Bluetooth settings on both sides"))
    return checks


def ram_gb() -> float | None:
    try:
        if platform.system() == "Darwin":
            return int(_run(["sysctl", "-n", "hw.memsize"]).strip()) / 2**30
        if hasattr(os, "sysconf"):
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError):
        pass
    return None


def model_ram_gb(model: str) -> float | None:
    tag = model.split(":")[-1].lower()
    for size, gb in MODEL_RAM_GB.items():
        if size in tag:
            return gb
    return None


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def format_checks(checks: list[Check]) -> str:
    marks = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}
    lines = []
    for c in checks:
        lines.append(f"[{marks[c.state]}] {c.name}: {c.detail}")
        if c.fix and c.state != OK:
            lines.append(f"           fix: {c.fix}")
    fails = sum(1 for c in checks if c.state == FAIL)
    warns = sum(1 for c in checks if c.state == WARN)
    if fails:
        lines.append(f"\n{fails} problem{'s' if fails != 1 else ''} to fix before running an agent.")
    elif warns:
        lines.append(f"\nReady, with {warns} thing{'s' if warns != 1 else ''} to know.")
    else:
        lines.append("\nReady.")
    return "\n".join(lines)


def main_sync(settings: Settings, **kw) -> list[Check]:
    return asyncio.run(run_checks(settings, **kw))
