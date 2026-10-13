"""Device A — Pump Agent.

Knows real-time machine health, sensor measurements, and recent operating
trends. When its local data shows an abnormal vibration pattern it looks for
a nearby peer offering ``maintenance_diagnostics``, collaborates on the
diagnosis (providing extra measurements on request), cross-checks the peer's
hypothesis against its own sensor trends, and produces the final structured
recommendation.

Run standalone on one laptop:

    python pump_agent.py --mdns                        # discover peers via mDNS
    python pump_agent.py --peer 192.168.1.20:8771      # or connect directly
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from common import banner, load_knowledge, pairing_secret, setup_logging

from loquor import Agent, PeerInfo
from loquor.discovery import PeerAddress, StaticDiscovery, ZeroconfDiscovery
from loquor.reasoning import Reasoner, best_available_reasoner
from loquor.transports import HttpTransport

AGENT_ID = "pump-01"


def build_pump_agent(
    *,
    reasoner: Reasoner,
    discovery=None,
    port: int = 0,
    store_path: str = ":memory:",
) -> Agent:
    sensors = load_knowledge("pump_sensors.json")
    agent = Agent(
        AGENT_ID,
        pairing_secret=pairing_secret(),
        transport=HttpTransport(port=port),
        discovery=discovery or StaticDiscovery(),
        store_path=store_path,
    )
    agent.sensors = sensors  # type: ignore[attr-defined]

    async def provide_measurements(
        task_id: str, needed: list[str], reason: str
    ) -> dict[str, Any]:
        """Answer a peer's info_request from the local (simulated) sensors."""
        print(f"[{AGENT_ID}] peer asked for {needed}: {reason}")
        available = sensors["on_demand_measurements"]
        provided = {key: available[key] for key in needed if key in available}
        print(f"[{AGENT_ID}] measured locally: {json.dumps(provided)}")
        return provided

    agent.info_provider = provide_measurements
    return agent


def detect_anomaly(sensors: dict) -> dict[str, Any] | None:
    """Deterministic local anomaly detection on the vibration trend."""
    latest = sensors["trend"][-1]
    if latest["vibration_mm_s"] <= sensors["vibration_alarm_mm_s"]:
        return None
    return {
        "asset_id": sensors["asset_id"],
        "vibration_mm_s": latest["vibration_mm_s"],
        "temperature_c": latest["temperature_c"],
        "alarm_threshold_mm_s": sensors["vibration_alarm_mm_s"],
        "trend_last_points": sensors["trend"][-4:],
    }


def crosscheck_hypothesis(sensors: dict, peer_result: dict[str, Any]) -> dict[str, Any]:
    """Device A verifies the peer's hypothesis against its own trend data."""
    trend = [point["vibration_mm_s"] for point in sensors["trend"]]
    steadily_rising = all(a <= b for a, b in zip(trend[-4:], trend[-3:]))
    spectrum = sensors["on_demand_measurements"]["vibration_spectrum"]
    two_x_dominant = spectrum["2x_rpm_mm_s"] > spectrum["1x_rpm_mm_s"]

    if peer_result["hypothesis"] == "shaft_misalignment":
        consistent = steadily_rising and two_x_dominant
    else:
        consistent = True  # no local evidence against other hypotheses
    return {
        "consistent_with_local_trends": consistent,
        "local_checks": {
            "vibration_steadily_rising": steadily_rising,
            "2x_rpm_dominant": two_x_dominant,
        },
    }


async def run_diagnosis(
    agent: Agent, reasoner: Reasoner, *, peer: PeerInfo | None = None, timeout: float = 15.0
) -> dict[str, Any]:
    """The full Device A side of the scenario, start to finish."""
    sensors = agent.sensors  # type: ignore[attr-defined]

    banner("1. Device A detects a problem")
    observations = detect_anomaly(sensors)
    if observations is None:
        print(f"[{AGENT_ID}] all sensor readings nominal, nothing to do")
        return {}
    print(
        f"[{AGENT_ID}] abnormal vibration: {observations['vibration_mm_s']} mm/s "
        f"(alarm at {observations['alarm_threshold_mm_s']} mm/s). "
        "Local knowledge is insufficient to find the cause."
    )

    banner("2. Looking for a nearby peer with maintenance_diagnostics")
    if peer is None:
        peers = await agent.find_peers(capability="maintenance_diagnostics", timeout=timeout)
        if not peers:
            raise SystemExit(f"[{AGENT_ID}] no diagnostic peer became reachable")
        peer = peers[0]
    print(f"[{AGENT_ID}] found {peer.agent_id} advertising {list(peer.capabilities)}")

    banner("3-4. Agents negotiate and contribute to the diagnosis")
    peer_result = await agent.ask(peer, "maintenance_diagnostics", observations)
    print(f"[{AGENT_ID}] peer hypothesis: {peer_result['hypothesis']} "
          f"(confidence {peer_result['confidence']})")

    crosscheck = crosscheck_hypothesis(sensors, peer_result)
    summary = await reasoner.complete(
        system=(
            "You are the pump's on-device assistant. In 2-3 sentences, state the "
            "final recommendation for the operator."
        ),
        prompt=(
            f"Peer diagnosis {peer_result['hypothesis']} with evidence "
            f"{peer_result['indicators']}; local cross-check {crosscheck}. "
            f"Repair instruction: {peer_result['repair_instruction']}"
        ),
    )

    recommendation = {
        "diagnosis": peer_result["hypothesis"],
        "confidence": peer_result["confidence"] if crosscheck["consistent_with_local_trends"] else "medium",
        "repair_instruction": peer_result["repair_instruction"],
        "evidence": {
            "observations": observations,
            "peer_indicators": peer_result["indicators"],
            "peer_evidence_records": peer_result["evidence_records"],
            "local_crosscheck": crosscheck,
        },
        "explanations": {
            peer.agent_id: peer_result["explanation"],
            AGENT_ID: summary,
        },
        "produced_by": [AGENT_ID, peer.agent_id],
    }

    banner("Structured diagnostic recommendation")
    print(json.dumps(recommendation, indent=2))
    return recommendation


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mdns", action="store_true", help="discover peers via mDNS/zeroconf")
    parser.add_argument("--peer", help="direct peer address host:port (skips discovery)")
    parser.add_argument("--store", default="pump-01.db", help="SQLite task store path")
    args = parser.parse_args()

    setup_logging()
    reasoner = await best_available_reasoner()
    banner(f"Pump Agent ({AGENT_ID}) — reasoner: {reasoner.name}")

    discovery = ZeroconfDiscovery() if args.mdns else StaticDiscovery()
    if args.peer:
        host, _, port = args.peer.rpartition(":")
        discovery = StaticDiscovery(
            [
                PeerAddress(
                    agent_id="inspector-01",
                    host=host,
                    port=int(port),
                    capabilities=("maintenance_diagnostics",),
                )
            ]
        )
    elif not args.mdns:
        parser.error("choose --mdns or --peer host:port")

    agent = build_pump_agent(reasoner=reasoner, discovery=discovery, store_path=args.store)
    await agent.start()
    try:
        await run_diagnosis(agent, reasoner)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
