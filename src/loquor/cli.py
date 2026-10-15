"""``loquor`` command line: run one agent from a profile.

    loquor run weather_agent.json                      # localhost (default)
    loquor run clothing_agent.json --transport wifi    # other laptops on the LAN
    python -m loquor run ...                           # same, without the script

The agent advertises itself, listens to everything, answers requests
addressed to its uid and — if its profile has a goal — asks the agents it
hears. It runs until Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from loquor.broadcast import TRANSPORTS, make_bus
from loquor.broadcast_agent import AgentProfile, BroadcastAgent
from loquor.reasoning import DEFAULT_MODEL, OllamaReasoner, ollama_available


async def run(args: argparse.Namespace) -> None:
    profile = AgentProfile.from_file(args.profile)
    model = args.model or profile.model or DEFAULT_MODEL
    if not await ollama_available(model=model):
        sys.exit(
            f"model {model!r} is not available. Start Ollama and `ollama pull {model}`, "
            "or choose another model with --model"
        )
    bus = make_bus(args.transport, interface=args.interface)
    agent = BroadcastAgent(
        profile,
        OllamaReasoner(model=model),
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
        f"{agent.uid} | model {model} | transport {args.transport} ({where}) | Ctrl-C to stop",
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await agent.stop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loquor", description="offline agent-to-agent communication")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run one agent from a profile JSON")
    run_p.add_argument("profile", help="agent profile JSON (name, description, capabilities, system_prompt, ...)")
    run_p.add_argument("--transport", choices=TRANSPORTS, default="localhost")
    run_p.add_argument("--interface", help="wifi: local IP of the network interface to use")
    run_p.add_argument("--model", help="Ollama model (overrides the profile and LOQUOR_MODEL)")
    run_p.add_argument("--advert-interval", type=float, default=10.0, help="seconds between adverts (default 10)")
    run_p.add_argument(
        "--wire",
        choices=("all", "no-adverts", "off"),
        default="all",
        help="message log: every message sent/received (default), all but adverts, or none",
    )
    run_p.add_argument("-v", "--verbose", action="store_true", help="also log duplicate deliveries")

    args = parser.parse_args(argv)
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
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, NotImplementedError) as exc:
        sys.exit(f"loquor: {exc}")


if __name__ == "__main__":
    main()
