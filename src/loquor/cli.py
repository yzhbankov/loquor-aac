"""Run one loquor agent from a profile JSON.

    python run_agent.py weather_agent.json                    # localhost (default)
    python run_agent.py clothing_agent.json --transport wifi  # other machines on the LAN

The agent beacons (unless quiet/silent), handshakes with the peers it hears,
answers requests for its skills' capabilities and runs its skills' background
loops. It runs until Ctrl-C. The LLM is a model served by a locally running
Ollama unless the profile chooses another reasoner.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from loquor.config import build_agent, load_profile
from loquor.protocol.discovery import Visibility
from loquor.reasoning import OllamaReasoner, ollama_available
from loquor.transports import TRANSPORTS

HANDSHAKE_TYPES = ("HELLO", "HELLO_ACK", "ACK")


async def run(args: argparse.Namespace) -> None:
    # Ctrl-C, `kill` or a closed terminal cancel this task, so every `finally`
    # below runs and the agent leaves the channel cleanly.
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, task.cancel)

    agent = build_agent(
        load_profile(args.profile),
        transport=args.transport,
        interface=args.interface,
        model=args.model,
        visibility=args.visibility,
        beacon_interval=args.beacon_interval,
    )
    reasoner = agent.reasoner
    if isinstance(reasoner, OllamaReasoner) and not await ollama_available(
        reasoner.base_url, model=reasoner.model
    ):
        sys.exit(
            f"model {reasoner.model!r} is not available in Ollama. Start Ollama and "
            f"`ollama pull {reasoner.model}`, or choose another model with --model"
        )

    await agent.start()
    where = getattr(agent.transport, "interface", "?")
    model = getattr(reasoner, "model", reasoner.name)
    print(
        f"{agent.uid} | id {agent.agent_id.hex()} | model {model} ({reasoner.name}) | "
        f"transport {args.transport or 'profile'} ({where}) | {agent.node.visibility.value} | Ctrl-C to stop",
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await agent.stop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="run_agent.py", description="run one loquor agent from a profile JSON"
    )
    parser.add_argument("profile", help="agent profile JSON (see docs/ARCHITECTURE.md)")
    parser.add_argument("--transport", choices=TRANSPORTS, help="override the profile's transport")
    parser.add_argument("--interface", help="wifi: local IP of the network interface to use")
    parser.add_argument("--model", help="override the profile's Ollama model")
    parser.add_argument("--visibility", choices=[v.value for v in Visibility], help="override the profile's visibility")
    parser.add_argument("--beacon-interval", type=float, default=10.0, help="seconds between beacons (default 10)")
    parser.add_argument(
        "--wire",
        choices=("all", "no-handshake", "off"),
        default="all",
        help="envelope log: every envelope (default), all but HELLO/HELLO_ACK/ACK, or none",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="also log beacons and duplicates")

    args = parser.parse_args(argv)
    try:
        _setup_logging(args)
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # stopped by a signal; cleanup already ran
    except (OSError, ValueError, NotImplementedError) as exc:
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
    elif args.wire == "no-handshake":
        # _log_wire passes the envelope type name as the second format argument
        wire.addFilter(
            lambda record: not (isinstance(record.args, tuple) and record.args[1:2] and record.args[1] in HANDSHAKE_TYPES)
        )


if __name__ == "__main__":
    main()
