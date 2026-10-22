"""Let peers fire this device's triggers: a node that acts, not only measures.

The skill advertises the triggers in the capability card — name,
description and input schema, under ``"triggers"`` in its skill entry — so
an agent that meets the node can hand them to its LLM like its own.
A request ``{"trigger": name, "input": {...}}`` is validated against the
trigger's schema by this device's trigger hub and then fired; the result
goes back as ``{"trigger": name, "result": {...}}``. No LLM is involved
on this side, so a node can offer actions.

Who may fire them is ``min_trust``: 0 lets any agent in range do it,
which is right for a demo and wrong for a pump on a real farm. Set 1 and
pin the controller's key (``~/.loquor/pinned_keys.json``) so only it can.
"""

from __future__ import annotations

from typing import Any

from loquor.agent.context import Context
from loquor.agent.skill import Skill


class OfferTriggers(Skill):
    capability = "fire_trigger"
    description = "Fire one of this device's triggers (listed in the capability card) with a validated input."
    input_schema = {
        "type": "object",
        "properties": {"trigger": {"type": "string"}, "input": {"type": "object"}},
        "required": ["trigger"],
    }
    output_schema = {"type": "object", "properties": {"trigger": {"type": "string"}, "result": {"type": "object"}}}
    needs_reasoner = False

    def __init__(self, triggers: list[str] | None = None, min_trust: int = 0):
        self.triggers = tuple(triggers or ())  # () = every trigger the device has
        self.min_trust = min_trust

    def offered(self, hub) -> list[str]:
        return [n for n in (self.triggers or hub.local_names()) if n in hub]

    def advertise(self, agent: Any) -> dict[str, Any]:
        hub = agent.triggers
        return {"triggers": {
            name: {"description": hub.get(name).description, "input_schema": hub.get(name).input_schema}
            for name in self.offered(hub)
        }}

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        name = str(input.get("trigger") or "")
        offered = self.offered(ctx.triggers)
        if name not in offered:
            raise ValueError(f"no such trigger: {name!r}; offered: {', '.join(offered) or 'none'}")
        data = input.get("input") or {}
        if not isinstance(data, dict):
            raise ValueError("input must be an object")
        result = await ctx.triggers.fire(name, data)  # validates against the schema, then runs
        return {"trigger": name, "result": result}
