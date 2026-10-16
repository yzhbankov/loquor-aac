"""Pull readings from every node in range, straight into this agent.

The hub-and-spokes case: small devices that only measure (**nodes**:
sensors + ``sensor_report``, no LLM) and one device that thinks. Every
``interval`` seconds this skill asks each handshaken peer that offers the
capability (``sensor_report`` by default) for its readings. The answer is
structured data — ``{"readings": {name: {value, unit, at}}}`` — sent in an
encrypted envelope directly from the node to this agent; no model on
either side touches it. The latest readings per node are kept in
``self.collected`` and shown by ``loquor status``.

With a ``goal`` the skill also thinks, once per round, after the
readings are in: the reasoner gets a table of every node's readings plus
this agent's own sensors and completes the goal; with ``triggers`` it may
propose one of them, validated by the trigger hub, and fire it. Without a
goal it only collects (and needs no LLM itself).

Other agents can ask this one for the whole table through the
``collected_readings`` capability.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill, with_sensors
from loquor.agent.tracker import RequestError
from loquor.protocol.discovery import Peer
from loquor.triggers.base import TriggerError


class Collect(Skill):
    capability = "collected_readings"
    description = "The latest readings collected from every sensor node in range."
    output_schema = {"type": "object", "properties": {"nodes": {"type": "object"}}, "required": ["nodes"]}

    def __init__(self, interval: float = 30.0, capability: str = "sensor_report",
                 peers: list[str] | None = None, goal: str = "", system_prompt: str = "",
                 temperature: float = 0.2, triggers: list[str] | None = None,
                 sensors: list[str] | None = None, min_nodes: int = 1):
        self.interval = interval
        self.ask_capability = capability
        self.peers = tuple(peers or ())  # node names to accept; () = every peer offering the capability
        self.goal = goal
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.triggers = tuple(triggers or ())
        self.sensors = tuple(sensors or ())  # own sensors to put beside the nodes' readings
        self.min_nodes = max(1, min_nodes)
        self.needs_reasoner = bool(goal)
        self.collected: dict[str, dict[str, Any]] = {}  # node uid -> {name, at, readings}
        self.rounds = 0
        self.assessment: str | None = None
        self.collected_event = asyncio.Event()  # set after every round, for tests and skills

    # -------------------------------------------------------------- answer

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        return {"nodes": self.collected}

    # ---------------------------------------------------------------- loop

    async def run(self, ctx: Context) -> None:
        while True:
            nodes = [p for p in ctx.peers(self.ask_capability) if self._accepts(p)]
            if nodes:
                await asyncio.gather(*(self._pull(peer, ctx) for peer in nodes))
                self.rounds += 1
                if self.goal and len(self.collected) >= self.min_nodes:
                    await self._think(ctx)
                self.collected_event.set()
                await asyncio.sleep(self.interval)
            else:
                try:
                    await asyncio.wait_for(ctx.wait_for_peers(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass

    def _accepts(self, peer: Peer) -> bool:
        return not self.peers or peer.name in self.peers or peer.uid in self.peers

    async def _pull(self, peer: Peer, ctx: Context) -> None:
        try:
            output = await ctx.ask(peer, self.ask_capability, {})
        except (TimeoutError, RequestError) as exc:
            ctx.log("no readings from %s: %s", peer.label, exc)
            return
        readings = output.get("readings")
        if not isinstance(readings, dict):
            ctx.log("%s answered without readings: %s", peer.label, json.dumps(output)[:120])
            return
        self.collected[peer.uid] = {"name": peer.name, "label": peer.label, "at": time.time(), "readings": readings}
        ctx.log("collected from %s: %s", peer.label, summarize(readings))

    # --------------------------------------------------------------- think

    async def _think(self, ctx: Context) -> None:
        table = self.table()
        system = self.system_prompt or "You run a device that watches sensor nodes nearby."
        if self.sensors:
            own = await ctx.sensors.readings(self.sensors)
            system = with_sensors(system, ctx.sensors.describe(own))
        situation = f"Your goal: {self.goal}\n\nReadings from the sensor nodes in range:\n{table}"
        if self.triggers:
            proposal = await ctx.triggers.propose(
                system=system,
                situation=situation + "\n\nDecide whether to act now with one of the available triggers, "
                                      "filling its input from the readings, or choose null if nothing is needed.",
                allowed=self.triggers, temperature=self.temperature,
            )
            self.assessment = proposal.reason
            if not proposal:
                ctx.log("round %d: no action — %s", self.rounds, proposal.reason)
                return
            ctx.log("round %d: firing %s — %s", self.rounds, proposal.trigger, proposal.reason)
            try:
                await ctx.triggers.fire(proposal.trigger, proposal.input)
            except TriggerError as exc:
                ctx.log("trigger failed: %s", exc)
            return
        self.assessment = (await ctx.reasoner.complete(
            system=system, prompt=situation + "\n\nAssess the situation in two or three short sentences.",
            temperature=self.temperature,
        )).strip()
        ctx.log("round %d: %s", self.rounds, self.assessment)

    def table(self) -> str:
        """Prompt-ready lines: one node per line, its readings with units and age."""
        lines = []
        for node in self.collected.values():
            lines.append(f"- {node['name']}: {summarize(node['readings'])}")
        return "\n".join(lines) or "(no nodes heard yet)"


def summarize(readings: dict[str, Any]) -> str:
    parts = []
    for name, r in readings.items():
        if not isinstance(r, dict):
            parts.append(f"{name}={r}")
            continue
        if r.get("error"):
            parts.append(f"{name}: unavailable ({r['error']})")
            continue
        value = r.get("value")
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        age = f" ({max(0.0, time.time() - float(r['at'])):.0f} s ago)" if r.get("at") else ""
        parts.append(f"{name}={value} {r.get('unit', '')}".rstrip() + age)
    return "; ".join(parts) or "(no readings)"
