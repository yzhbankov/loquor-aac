"""``loquor`` command line.

    loquor run weather_agent.json                      # localhost (default)
    loquor run clothing_agent.json --transport wifi    # other laptops on the LAN
    loquor model list | pull <name> | import <file.gguf>
    loquor version
    python -m loquor ...                               # same, without the script

``run`` starts one agent: it advertises itself, listens to everything,
answers requests addressed to its uid and — if its profile has a goal — asks
the agents it hears. It runs until Ctrl-C.

The LLM runs inside loquor (embedded llama.cpp engine) by default; a catalog
model is downloaded on first use. ``--engine ollama`` uses a separately
installed Ollama instead.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import signal
import sys

from loquor import __version__, models
from loquor.broadcast import TRANSPORTS, make_bus
from loquor.broadcast_agent import AgentProfile, BroadcastAgent
from loquor.engine import EngineError, LlamaCppReasoner, LlamaServer, find_llama_server
from loquor.reasoning import DEFAULT_MODEL as DEFAULT_OLLAMA_MODEL
from loquor.reasoning import OllamaReasoner, Reasoner, ollama_available


async def _embedded_reasoner(model: str) -> tuple[Reasoner, LlamaServer, str]:
    if ":" in model and not model.endswith(".gguf"):
        raise models.ModelError(
            f"{model!r} looks like an Ollama model name; use --engine ollama, "
            f"or a loquor model such as {models.DEFAULT_MODEL_NAME!r}"
        )
    try:
        path = models.resolve(model)
    except models.ModelError:
        if model not in models.CATALOG or models.installed_path(model).exists():
            raise
        print(f"first use of {model!r}: downloading it once, then loquor runs offline", flush=True)
        path = await models.pull(model)
    server = LlamaServer(path)
    print(f"starting the local model {path.name} ...", flush=True)
    await server.start()
    return LlamaCppReasoner(server.base_url, model=model), server, model


async def run(args: argparse.Namespace) -> None:
    # Ctrl-C, `kill` or a closed terminal cancel this task, so every `finally`
    # below runs and the embedded engine is stopped with the agent.
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, task.cancel)

    profile = AgentProfile.from_file(args.profile)
    server: LlamaServer | None = None
    if args.engine == "ollama":
        model = args.model or profile.model or DEFAULT_OLLAMA_MODEL
        if not await ollama_available(model=model):
            sys.exit(
                f"model {model!r} is not available in Ollama. Start Ollama and "
                f"`ollama pull {model}`, or choose another model with --model"
            )
        reasoner: Reasoner = OllamaReasoner(model=model)
    else:
        model = args.model or profile.model or models.DEFAULT_MODEL_NAME
        reasoner, server, model = await _embedded_reasoner(model)

    try:
        bus = make_bus(args.transport, interface=args.interface)
        agent = BroadcastAgent(
            profile,
            reasoner,
            pairing_secret=os.environ.get("LOQUOR_PAIRING_SECRET", "demo-pairing-secret"),
            bus=bus,
            advert_interval=args.advert_interval,
        )

        async def show(result: dict) -> None:
            print(
                "\n=== Goal complete ===\n"
                f"goal      : {result['goal']}\n"
                f"asked     : {result['asked']['name']} ({result['asked']['uid']})\n"
                f"why       : {result['reasoning']}\n"
                f"question  : {result['asked']['question']}\n"
                f"answer    : {result['answer']}\n"
                f"outcome   : {result['outcome']}\n",
                flush=True,
            )

        agent.on_result(show)
        await agent.start()
        where = getattr(bus, "interface", "?")
        print(
            f"{agent.uid} | model {model} ({args.engine}) | transport {args.transport} "
            f"({where}) | Ctrl-C to stop",
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await agent.stop()
    finally:
        if server is not None:
            await server.stop()


def model_command(args: argparse.Namespace) -> None:
    if args.model_command == "list":
        installed = {p.stem: p for p in models.installed_models()}
        print(f"models directory: {models.models_dir()}")
        for name, spec in models.CATALOG.items():
            state = "installed" if name in installed else f"not installed ({spec.size / 1e9:.1f} GB)"
            default = "  [default]" if name == models.DEFAULT_MODEL_NAME else ""
            print(f"  {name:<16} {state:<26} {spec.description}{default}")
        for name, path in installed.items():
            if name not in models.CATALOG:
                print(f"  {name:<16} {'installed':<26} imported from a file")
    elif args.model_command == "pull":
        path = asyncio.run(models.pull(args.name))
        print(f"{args.name} ready: {path}")
    elif args.model_command == "import":
        print("verifying and copying the file ...", flush=True)
        name, path = models.import_file(args.file, args.name)
        print(f"imported as {name!r}: {path}\nuse it with: loquor run <profile> --model {name}")


def version_command(_: argparse.Namespace) -> None:
    try:
        engine = str(find_llama_server())
    except EngineError:
        engine = "not found"
    print(f"loquor {__version__} ({platform.system().lower()}-{platform.machine()})")
    print(f"engine: {engine}")
    print(f"home:   {models.loquor_home()}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loquor", description="offline agent-to-agent communication")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run one agent from a profile JSON")
    run_p.add_argument("profile", help="agent profile JSON (name, description, capabilities, system_prompt, ...)")
    run_p.add_argument("--transport", choices=TRANSPORTS, default="localhost")
    run_p.add_argument("--interface", help="wifi: local IP of the network interface to use")
    run_p.add_argument(
        "--model",
        help=f"model name or .gguf path (default: the profile's, else {models.DEFAULT_MODEL_NAME})",
    )
    run_p.add_argument(
        "--engine",
        choices=("embedded", "ollama"),
        default="embedded",
        help="embedded llama.cpp (default, nothing to install) or an installed Ollama",
    )
    run_p.add_argument("--advert-interval", type=float, default=10.0, help="seconds between adverts (default 10)")
    run_p.add_argument(
        "--wire",
        choices=("all", "no-adverts", "off"),
        default="all",
        help="message log: every message sent/received (default), all but adverts, or none",
    )
    run_p.add_argument("-v", "--verbose", action="store_true", help="also log duplicate deliveries")
    run_p.set_defaults(handler=None)

    model_p = sub.add_parser("model", help="manage local model files")
    model_sub = model_p.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list", help="catalog and installed models")
    pull_p = model_sub.add_parser("pull", help="download a catalog model once (needs internet)")
    pull_p.add_argument("name", nargs="?", default=models.DEFAULT_MODEL_NAME)
    import_p = model_sub.add_parser("import", help="install a .gguf file copied by hand (offline machines)")
    import_p.add_argument("file")
    import_p.add_argument("--name", help="name to install it under (default: detected or file name)")
    model_p.set_defaults(handler=model_command)

    version_p = sub.add_parser("version", help="show version, engine and data locations")
    version_p.set_defaults(handler=version_command)

    args = parser.parse_args(argv)
    try:
        if args.handler is not None:
            args.handler(args)
            return
        _setup_logging(args)
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # stopped by a signal; cleanup already ran
    except (OSError, ValueError, NotImplementedError, models.ModelError, EngineError) as exc:
        sys.exit(f"loquor: {exc}")


def _setup_logging(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    wire = logging.getLogger("loquor.wire")
    if args.wire == "off":
        wire.setLevel(logging.WARNING)
    elif args.wire == "no-adverts":
        # _log_wire passes the message type as the second format argument
        wire.addFilter(
            lambda record: not (isinstance(record.args, tuple) and record.args[1:2] == ("advert",))
        )


if __name__ == "__main__":
    main()
