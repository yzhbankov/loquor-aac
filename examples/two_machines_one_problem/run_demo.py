"""Single-machine runner for the Two Machines, One Problem demo.

Runs both device agents in one process over the loopback interface, so the
whole scenario can be demonstrated (and CI-tested) on one machine with no
multicast or second laptop required:

    python run_demo.py              # full collaborative diagnosis
    python run_demo.py --interrupt  # Test 2: disconnect B mid-task, resume

For the real two-laptop setup, run ``inspection_agent.py --mdns`` on one
machine and ``pump_agent.py --mdns`` (or ``--peer host:port``) on the other.
"""

from __future__ import annotations

import argparse
import asyncio

from common import banner, setup_logging
from inspection_agent import build_inspection_agent
from pump_agent import build_pump_agent, run_diagnosis

from loquor import PeerInfo
from loquor.discovery import PeerAddress, StaticDiscovery
from loquor.messages import TaskState
from loquor.reasoning import best_available_reasoner
from loquor.transports import PeerUnreachable


def _peer_for(agent) -> PeerInfo:
    address = PeerAddress(
        agent_id=agent.agent_id,
        host="127.0.0.1",
        port=agent.port,
        capabilities=tuple(sorted(agent._capabilities)),
    )
    return PeerInfo(agent_id=agent.agent_id, address=address)


async def demo(interrupt: bool, inspector_store: str) -> None:
    reasoner = await best_available_reasoner()
    banner(f"Two Machines, One Problem — reasoner: {reasoner.name}")

    inspector = build_inspection_agent(reasoner=reasoner, store_path=inspector_store)
    pump = build_pump_agent(reasoner=reasoner)
    await inspector.start()
    await pump.start()

    # Loopback stand-in for mDNS discovery: announce the inspector to the pump.
    await pump.discovery.add_peer(_peer_for(inspector).address)  # type: ignore[attr-defined]

    try:
        if not interrupt:
            await run_diagnosis(pump, reasoner, peer=_peer_for(inspector))
            return

        # ---- Test 2: interrupted collaboration --------------------------
        banner("Test 2 — Device B disconnects mid-task")
        original_provider = pump.info_provider

        async def provider_then_disconnect(task_id, needed, reason):
            measurements = await original_provider(task_id, needed, reason)
            print("[demo] >>> Device B disconnects before the answer is delivered <<<")
            await inspector.transport.stop()
            return measurements

        pump.info_provider = provider_then_disconnect
        try:
            await run_diagnosis(pump, reasoner, peer=_peer_for(inspector))
            raise AssertionError("expected the peer to be unreachable")
        except PeerUnreachable:
            pass
        task = pump.store.by_state("requester", TaskState.INTERRUPTED)[0]
        print(f"[demo] pump saved interrupted task {task['task_id']} "
              f"(state: {task['state'].value})")

        banner("Device B reconnects — task resumes with no duplicate results")
        pump.info_provider = original_provider
        # Restart B with a fresh transport but the SAME persistent store.
        restarted = build_inspection_agent(
            reasoner=reasoner, port=0, store_path=inspector_store
        )
        await restarted.start()
        try:
            results = await pump.resume_pending(_peer_for(restarted))
            assert len(results) == 1, "exactly one resumed result expected"
            print(f"[demo] resumed diagnosis: {results[0]['hypothesis']} "
                  f"(confidence {results[0]['confidence']})")
            resumed = pump.store.get(task["task_id"])
            print(f"[demo] task {task['task_id']} final state: {resumed['state'].value}")
        finally:
            await restarted.stop()
    finally:
        await pump.stop()
        try:
            await inspector.stop()
        except Exception:
            pass  # already stopped in the interrupt scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interrupt",
        action="store_true",
        help="demonstrate disconnection mid-task and resume on reconnect",
    )
    parser.add_argument("--quiet", action="store_true", help="less log output")
    args = parser.parse_args()
    setup_logging(verbose=not args.quiet)

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(demo(args.interrupt, str(Path(tmp) / "inspector.db")))


if __name__ == "__main__":
    main()
