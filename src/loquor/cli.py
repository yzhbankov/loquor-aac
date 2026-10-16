"""The ``loquor`` command.

    loquor doctor                       is this device ready? (python, ollama, model, radio)
    loquor init my_agent                a working agent to start from
    loquor check my_agent/agent.json    build it, read its sensors, check the model — nothing on the air
    loquor run   my_agent/agent.json    run it (Ctrl-C to stop)
    loquor status                       what the running agents see, read and decided
    loquor trace "My Agent"             every LLM call of the last run
    loquor config show|set|path         this device's transport, model, beacon interval, logging
    loquor service install agent.json   start it at boot (systemd / launchd)

Transport, model, beacon interval and logging come from the device config
(``~/.loquor/config.json``); a profile may pin them; the command line
overrides both. ``python run_agent.py profile.json`` still works and means
``loquor run``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from loquor import __version__
from loquor.protocol.discovery import Visibility
from loquor.settings import WIRE_LEVELS, Settings, config_path
from loquor.transports import TRANSPORTS

HANDSHAKE_TYPES = ("HELLO", "HELLO_ACK", "ACK")
COMMANDS = ("run", "check", "doctor", "init", "status", "trace", "config", "service", "version")


# ------------------------------------------------------------------ run


async def run(args: argparse.Namespace) -> None:
    from loquor.config import build_agent, load_profile  # noqa: PLC0415
    from loquor.reasoning import OllamaReasoner, TracingReasoner, ollama_available  # noqa: PLC0415
    from loquor.tools.status import StatusWriter, trace_path  # noqa: PLC0415

    # Ctrl-C, `kill` or a closed terminal cancel this task, so every `finally`
    # below runs and the agent leaves the channel cleanly.
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, task.cancel)

    settings = Settings.load()
    profile = load_profile(args.profile)
    trace = settings.trace if args.trace is None else args.trace
    agent = build_agent(
        profile,
        base_dir=Path(args.profile).resolve().parent,
        settings=settings,
        wrap_reasoner=(lambda r: r if r.name == "none" else TracingReasoner(r, trace_path(profile["name"]))) if trace else None,
        **_overrides(args),
    )
    reasoner = getattr(agent.reasoner, "inner", agent.reasoner)
    if isinstance(reasoner, OllamaReasoner) and not settings.llm:
        sys.exit(
            f"loquor: this device is configured as a sensor node (llm false), but {Path(args.profile).name} "
            "needs an LLM. Run it on a device with Ollama, or `loquor config set llm true` and install Ollama here."
        )
    if isinstance(reasoner, OllamaReasoner) and not await ollama_available(reasoner.base_url, model=reasoner.model):
        sys.exit(
            f"loquor: model {reasoner.model!r} is not available in Ollama at {reasoner.base_url}.\n"
            f"  Start Ollama and `ollama pull {reasoner.model}`, choose another with --model or "
            f"`loquor config set model <name>`, or run `loquor doctor` to see what is missing."
        )

    await agent.start()
    status = StatusWriter(agent, extra={"profile": str(Path(args.profile).resolve())})
    status.start()
    where = getattr(agent.transport, "interface", "?")
    model = getattr(reasoner, "model", reasoner.name)
    print(
        f"{agent.uid} | id {agent.agent_id.hex()} | model {model} ({reasoner.name}) | "
        f"transport {_transport_label(args, profile, settings)} ({where}) | {agent.node.visibility.value} | "
        f"Ctrl-C to stop\n"
        f"status: loquor status \"{agent.name}\""
        + (f" · trace: {trace_path(profile['name'])}" if trace and reasoner.name != "none" else ""),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await status.stop()
        await agent.stop()


def _overrides(args: argparse.Namespace) -> dict:
    return {
        "transport": getattr(args, "transport", None),
        "interface": getattr(args, "interface", None),
        "model": getattr(args, "model", None),
        "visibility": getattr(args, "visibility", None),
        "beacon_interval": getattr(args, "beacon_interval", None),
    }


def _transport_label(args: argparse.Namespace, profile: dict, settings: Settings) -> str:
    return args.transport or (profile.get("transport") or {}).get("type") or settings.transport


# ---------------------------------------------------------------- check


def check(args: argparse.Namespace) -> int:
    from loquor.tools.check import check_profile  # noqa: PLC0415
    from loquor.tools.doctor import format_checks  # noqa: PLC0415

    lines, checks = asyncio.run(check_profile(Path(args.profile), Settings.load(), llm=not args.no_llm, **_overrides(args)))
    if lines:
        print("\n".join(lines), end="\n\n")
    print(format_checks(checks))
    return 0 if all(c.ok for c in checks) else 1


# --------------------------------------------------------------- doctor


def doctor(args: argparse.Namespace) -> int:
    from loquor.tools.doctor import format_checks, run_checks  # noqa: PLC0415

    try:
        settings = Settings.load()
    except ValueError as exc:
        print(f"[ FAIL ] device config: {exc}\n           fix: edit or delete {config_path()}")
        return 1
    checks = asyncio.run(run_checks(settings, transport=args.transport, llm=not args.no_llm))
    print(format_checks(checks))
    return 0 if all(c.ok for c in checks) else 1


# ----------------------------------------------------------------- init


def init(args: argparse.Namespace) -> int:
    from loquor.tools.scaffold import init as scaffold  # noqa: PLC0415

    directory = Path(args.directory)
    try:
        written = scaffold(directory, args.name, force=args.force, node=args.node)
    except FileExistsError as exc:
        print(f"loquor: {exc}")
        return 1
    print("\n".join(f"wrote {p}" for p in written))
    print(f"\nNext:\n  loquor check {directory / 'agent.json'}\n  loquor run   {directory / 'agent.json'}")
    return 0


# --------------------------------------------------------------- status


def status(args: argparse.Namespace) -> int:
    from loquor.tools.status import format_status, read_status, running  # noqa: PLC0415

    if args.target:
        data = read_status(args.target)
        if data is None:
            print(f"loquor: no status for {args.target!r}: not running, or started before this version")
            return 1
        found = [data]
    else:
        found = running()
        if not found:
            print("no agents running on this device (none has left a status file)")
            return 0
    if args.json:
        print(json.dumps(found if len(found) > 1 or not args.target else found[0], indent=2, ensure_ascii=False, default=str))
    else:
        print("\n\n".join(format_status(d) for d in found))
    return 0


def trace(args: argparse.Namespace) -> int:
    from loquor.reasoning import read_trace  # noqa: PLC0415
    from loquor.tools.status import read_status, trace_path  # noqa: PLC0415

    path = Path(args.target)
    if path.suffix == ".jsonl" and path.exists():
        pass
    elif path.suffix == ".json" and path.exists():
        try:
            path = trace_path(json.loads(path.read_text()).get("name", path.stem))
        except (ValueError, AttributeError):
            path = trace_path(path.stem)
    else:
        data = read_status(args.target)
        path = trace_path(data["name"] if data else args.target)
    calls = read_trace(path, last=args.last)
    if not calls:
        print(f"loquor: no trace at {path} (tracing off, or the agent never called the LLM)")
        return 1
    if args.json:
        print(json.dumps(calls, indent=2, ensure_ascii=False))
        return 0
    for c in calls:
        head = f"#{c.get('n')}  {c.get('seconds')} s  model {c.get('model')}  temperature {c.get('temperature')}"
        print(head + ("  (JSON schema requested)" if c.get("schema") else ""))
        if args.full:
            print("  system :", _indent(c.get("system", "")))
        print("  prompt :", _indent(c.get("prompt", "") if args.full else _snip(c.get("prompt", ""))))
        if c.get("error"):
            print("  error  :", c["error"])
        else:
            print("  reply  :", _indent(c.get("response", "") if args.full else _snip(c.get("response", ""))))
        print()
    print(f"{len(calls)} call{'s' if len(calls) != 1 else ''} from {path}" + ("" if args.full else "; --full for whole prompts"))
    return 0


def _snip(text: str, width: int = 200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _indent(text: str) -> str:
    return ("\n" + " " * 11).join(str(text).splitlines()) or ""


# --------------------------------------------------------------- config


def config(args: argparse.Namespace) -> int:
    if args.action == "path":
        print(config_path())
        return 0
    try:
        settings = Settings.load()
    except ValueError as exc:
        print(f"loquor: {exc}")
        return 1
    if args.action == "show":
        state = "" if config_path().exists() else f"   (defaults; nothing saved yet at {config_path()})"
        print(json.dumps(settings.to_dict(), indent=2) + state)
        return 0
    if args.action == "set":
        if len(args.pairs) % 2:
            print("loquor: config set takes KEY VALUE pairs")
            return 2
        try:
            for key, value in zip(args.pairs[::2], args.pairs[1::2]):
                settings.set(key, value)
        except ValueError as exc:
            print(f"loquor: {exc}")
            return 2
        path = settings.save()
        print(json.dumps(settings.to_dict(), indent=2) + f"\nsaved to {path}")
        return 0
    if args.action == "reset":
        if config_path().exists():
            config_path().unlink()
        print(f"removed {config_path()}; defaults apply")
        return 0
    return 2


# -------------------------------------------------------------- service


def service(args: argparse.Namespace) -> int:
    from loquor.config import load_profile  # noqa: PLC0415
    from loquor.tools import service as svc  # noqa: PLC0415

    try:
        if args.action == "install":
            profile = Path(args.profile)
            name = load_profile(profile)["name"]
            extra = []
            for flag in ("transport", "interface", "model", "visibility"):
                if getattr(args, flag, None):
                    extra += [f"--{flag}", str(getattr(args, flag))]
            if getattr(args, "beacon_interval", None):
                extra += ["--beacon-interval", str(args.beacon_interval)]
            result = svc.install(profile, name, extra)
            print(f"wrote {result.unit_path}")
        else:
            target = Path(args.profile)
            name = load_profile(target)["name"] if target.suffix == ".json" and target.exists() else args.profile
            result = svc.uninstall(name)
            if result.unit_path.exists():
                result.unit_path.unlink()
                print(f"removed {result.unit_path}")
    except (OSError, ValueError) as exc:
        print(f"loquor: {exc}")
        return 1
    if args.now:
        return svc.run_commands(result.commands)
    print("Then:\n  " + "\n  ".join(result.commands))
    return 0


# ----------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")  # `loquor profile.json` and `run_agent.py profile.json`
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return
    if args.command == "version":
        print(f"loquor {__version__}")
        return
    try:
        if args.command == "run":
            _setup_logging(args)
            asyncio.run(run(args))
            return
        code = {"check": check, "doctor": doctor, "init": init, "status": status, "trace": trace,
                "config": config, "service": service}[args.command](args)
        if code:
            sys.exit(code)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # stopped by a signal; cleanup already ran
    except (OSError, ValueError, NotImplementedError) as exc:
        sys.exit(f"loquor: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loquor", description="run and look after loquor agents on this device")
    parser.add_argument("--version", action="version", version=f"loquor {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    def overrides(p: argparse.ArgumentParser) -> None:
        p.add_argument("--transport", choices=TRANSPORTS, help="override the device config and the profile")
        p.add_argument("--interface", help="wifi: local IP of the network interface to use")
        p.add_argument("--model", help="override the Ollama model")
        p.add_argument("--visibility", choices=[v.value for v in Visibility], help="override the profile's visibility")
        p.add_argument("--beacon-interval", type=float, help="seconds between beacons (device config, else 10)")

    p = sub.add_parser("run", help="run an agent from a profile until Ctrl-C")
    p.add_argument("profile", help="agent profile JSON")
    overrides(p)
    p.add_argument("--wire", choices=WIRE_LEVELS, help="envelope log: off (default), no-handshake, all")
    p.add_argument("--trace", dest="trace", action="store_true", default=None, help="record every LLM call (default: device config)")
    p.add_argument("--no-trace", dest="trace", action="store_false", help="do not record LLM calls")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging: beacons, duplicates, sensor readings")

    p = sub.add_parser("check", help="build a profile and test its parts without going on the air")
    p.add_argument("profile")
    overrides(p)
    p.add_argument("--no-llm", action="store_true", help="skip the Ollama and model checks")

    p = sub.add_parser("doctor", help="check that this device can run an agent")
    p.add_argument("--transport", choices=TRANSPORTS, help="check this transport instead of the configured one")
    p.add_argument("--no-llm", action="store_true", help="skip the Ollama and model checks")

    p = sub.add_parser("init", help="write a starter agent (profile, prompt, a sensor, a trigger)")
    p.add_argument("directory", nargs="?", default="my_agent")
    p.add_argument("--name", help="the agent's name (default: from the directory)")
    p.add_argument("--node", action="store_true", help="a sensor node: sensors and sensor_report only, no LLM")
    p.add_argument("--force", action="store_true", help="overwrite existing files")

    p = sub.add_parser("status", help="what the running agents see, read and decided")
    p.add_argument("target", nargs="?", help="agent name or profile path (default: all)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("trace", help="the LLM calls of an agent's last run")
    p.add_argument("target", help="agent name, profile path or trace file")
    p.add_argument("-n", "--last", type=int, default=None, help="only the last N calls")
    p.add_argument("--full", action="store_true", help="whole system prompts, prompts and replies")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("config", help="this device's settings (~/.loquor/config.json)")
    p.add_argument("action", choices=("show", "set", "reset", "path"))
    p.add_argument("pairs", nargs="*", metavar="KEY VALUE", help="for set: transport, interface, model, ollama_url, beacon_interval, wire, trace, llm")

    p = sub.add_parser("service", help="run an agent at boot with systemd (Linux) or launchd (macOS)")
    p.add_argument("action", choices=("install", "uninstall"))
    p.add_argument("profile", help="profile path (uninstall: or the agent name)")
    overrides(p)
    p.add_argument("--now", action="store_true", help="also run the enable/start commands")

    sub.add_parser("version", help="print the version").set_defaults(command="version")
    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        wire_level = args.wire or Settings.load().wire
    except ValueError:
        wire_level = "off"
    wire = logging.getLogger("loquor.wire")
    if wire_level == "off":
        wire.setLevel(logging.WARNING)
    elif wire_level == "no-handshake":
        # _log_wire passes the envelope type name as the second format argument
        wire.addFilter(
            lambda record: not (isinstance(record.args, tuple) and record.args[1:2] and record.args[1] in HANDSHAKE_TYPES)
        )


if __name__ == "__main__":
    main()
