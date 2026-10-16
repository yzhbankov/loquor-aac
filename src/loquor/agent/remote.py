"""A peer's trigger, usable here as if it were our own.

A node that runs ``offer_triggers`` lists its triggers in its capability
card. :func:`remote_triggers_of` reads them; a :class:`RemoteTrigger` has
the same ``name``/``description``/``input_schema`` contract as a local
one, so the trigger hub validates the LLM's proposal here, and ``fire()``
sends ``fire_trigger`` to the node, which validates again and acts. The
hub name is ``<node>:<trigger>`` (``tank-node:valve``), so the LLM can
tell two nodes' pumps apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loquor.agent.tracker import RequestError
from loquor.protocol.discovery import Peer, slug
from loquor.triggers.base import Trigger, TriggerError

if TYPE_CHECKING:
    from loquor.agent.context import Context

CAPABILITY = "fire_trigger"


class RemoteTrigger(Trigger):
    def __init__(self, peer: Peer, trigger: str, description: str, input_schema: dict[str, Any], ctx: Context):
        self.peer = peer
        self.trigger = trigger
        self.name = remote_name(peer, trigger)
        self.description = f"{description} (on {peer.name})"
        self.input_schema = input_schema
        self._ctx = ctx

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await self._ctx.ask(self.peer, CAPABILITY, {"trigger": self.trigger, "input": input})
        except RequestError as exc:
            raise TriggerError(f"{self.peer.label} refused {self.trigger}: {exc.detail}") from exc
        except TimeoutError as exc:
            raise TriggerError(f"{self.peer.label} did not answer") from exc
        result = out.get("result")
        return result if isinstance(result, dict) else {"result": result}


def remote_name(peer: Peer, trigger: str) -> str:
    return f"{slug(peer.name) or peer.uid[:8]}:{trigger}"


def remote_triggers_of(peer: Peer) -> dict[str, dict[str, Any]]:
    """``{trigger: {description, input_schema}}`` a peer offers, from its card."""
    for entry in peer.card.get("skills", []):
        if isinstance(entry, dict) and entry.get("id") == CAPABILITY:
            offered = entry.get("triggers")
            if isinstance(offered, dict):
                return {
                    str(name): {
                        "description": str(spec.get("description", "")),
                        "input_schema": spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {"type": "object"},
                    }
                    for name, spec in offered.items() if isinstance(spec, dict)
                }
    return {}
