"""Device B — Inspection Agent.

Knows maintenance history, previous failures, and repair instructions.
Advertises the ``maintenance_diagnostics`` capability: given another
machine's observations it searches its local maintenance records, asks for
additional measurements when the evidence is insufficient, and returns a
structured diagnostic hypothesis with supporting references.

Run standalone on one laptop:

    python inspection_agent.py --mdns            # advertise via mDNS/zeroconf
    python inspection_agent.py --port 8771       # fixed port, no mDNS
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from common import banner, load_knowledge, pairing_secret, setup_logging

from loquor import Agent
from loquor.agent import NeedInfo, TaskContext
from loquor.discovery import StaticDiscovery, ZeroconfDiscovery
from loquor.reasoning import Reasoner, best_available_reasoner
from loquor.transports import HttpTransport

AGENT_ID = "inspector-01"
RECENT_WORK_TAGS = {"bearing", "coupling"}


def build_inspection_agent(
    *,
    reasoner: Reasoner,
    discovery=None,
    port: int = 0,
    store_path: str = ":memory:",
) -> Agent:
    knowledge = load_knowledge("maintenance_records.json")
    agent = Agent(
        AGENT_ID,
        pairing_secret=pairing_secret(),
        transport=HttpTransport(port=port),
        discovery=discovery or StaticDiscovery(),
        store_path=store_path,
    )

    async def diagnose(task: TaskContext) -> dict[str, Any] | NeedInfo:
        observations = task.observations
        asset_id = observations.get("asset_id", "unknown")

        # 1. Search local maintenance history for relevant recent work.
        relevant = [
            record
            for record in knowledge["records"]
            if record["asset_id"] == asset_id and RECENT_WORK_TAGS & set(record["tags"])
        ]

        # 2. The vibration level alone does not separate misalignment from
        #    bearing wear — ask the requesting device for the measurements
        #    only it can take locally.
        missing = [
            key
            for key in ("motor_current_a", "motor_current_nominal_a", "vibration_spectrum")
            if key not in task.measurements
        ]
        if missing:
            return NeedInfo(
                needed=missing,
                reason=(
                    "Maintenance history shows recent bearing/coupling work "
                    f"({', '.join(r['record_id'] for r in relevant) or 'none'}); "
                    "motor current and vibration spectrum are needed to "
                    "separate shaft misalignment from bearing wear."
                ),
            )

        # 3. Deterministic hypothesis selection from the combined evidence.
        current = float(task.measurements["motor_current_a"])
        nominal = float(task.measurements["motor_current_nominal_a"])
        spectrum = task.measurements["vibration_spectrum"]
        two_x_dominant = float(spectrum.get("2x_rpm_mm_s", 0)) > float(
            spectrum.get("1x_rpm_mm_s", 0)
        )
        current_elevated = nominal > 0 and current / nominal > 1.15

        indicators: list[str] = []
        if two_x_dominant:
            indicators.append("vibration dominant at 2x running speed")
        if current_elevated:
            indicators.append(
                f"motor current {current} A elevated vs nominal {nominal} A"
            )
        if relevant:
            indicators.append(
                "recent coupling/bearing work: "
                + "; ".join(f"{r['record_id']} ({r['date']}) {r['summary']}" for r in relevant)
            )

        if two_x_dominant and current_elevated and relevant:
            mode = next(
                m for m in knowledge["failure_modes"] if m["mode"] == "shaft_misalignment"
            )
            hypothesis, confidence = "shaft_misalignment", "high"
        elif not two_x_dominant:
            mode = next(m for m in knowledge["failure_modes"] if m["mode"] == "bearing_wear")
            hypothesis, confidence = "bearing_wear", "medium"
        else:
            mode = {"repair_instruction": "Inspect on site; evidence inconclusive."}
            hypothesis, confidence = "inconclusive", "low"

        # 4. The local LLM articulates the reasoning; it does not decide it.
        explanation = await reasoner.complete(
            system=(
                "You are a maintenance diagnostics assistant on an offline device. "
                "In 2-3 sentences, explain the hypothesis to a technician."
            ),
            prompt=(
                f"Asset {asset_id}: observations {observations}, "
                f"measurements {task.measurements}, indicators {indicators}. "
                f"Hypothesis: {hypothesis} (confidence {confidence})."
            ),
        )

        return {
            "hypothesis": hypothesis,
            "confidence": confidence,
            "indicators": indicators,
            "repair_instruction": mode["repair_instruction"],
            "evidence_records": [r["record_id"] for r in relevant],
            "explanation": explanation,
            "diagnosed_by": AGENT_ID,
        }

    agent.add_capability("maintenance_diagnostics", diagnose)
    return agent


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mdns", action="store_true", help="advertise via mDNS/zeroconf")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--store", default="inspector-01.db", help="SQLite task store path")
    args = parser.parse_args()

    setup_logging()
    reasoner = await best_available_reasoner()
    banner(f"Inspection Agent ({AGENT_ID}) — reasoner: {reasoner.name}")

    agent = build_inspection_agent(
        reasoner=reasoner,
        discovery=ZeroconfDiscovery() if args.mdns else StaticDiscovery(),
        port=args.port,
        store_path=args.store,
    )
    await agent.start()
    print(f"{AGENT_ID} serving maintenance_diagnostics on port {agent.port}. Ctrl-C to stop.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
