"""Skill interface: where business logic lives.

A skill can do either or both of:

- **answer** requests for its ``capability`` (``handle``). The capability is
  advertised in the agent's capability document, and requests naming it are
  dispatched here.
- **act** on its own (``run``): a background loop that watches sensors or
  peers and fires triggers or asks questions. A skill with no capability
  only runs.

Both receive a :class:`~loquor.agent.context.Context`: the reasoner, the
peers, ``ask()``, and the agent's sensors and triggers. A skill names the
sensors and triggers it uses in ``sensors`` / ``triggers``; the agent checks
at startup that they exist.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loquor.agent.context import Context


class Skill:
    #: Advertised to other agents; requests naming it are dispatched to
    #: ``handle``. Empty = this skill only runs, it answers nothing.
    capability: str = ""
    #: Optional entries for the capability document (AMP §6).
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    #: Minimum trust level a requester needs (AMP §9); enforced at dispatch.
    min_trust: int = 0
    #: Sensors and triggers this skill uses, by name; checked at startup.
    sensors: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    #: False for skills that never call the reasoner (``sensor_report``), so a
    #: profile made only of those runs as a *node*: no LLM, no Ollama.
    needs_reasoner: bool = True

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Answer one request. Raise to send the requester an error."""
        raise NotImplementedError(f"{type(self).__name__} does not answer requests")

    async def run(self, ctx: Context) -> None:
        """Optional background loop; cancelled when the agent stops."""


def fill_placeholders(prompt: str) -> str:
    """``{random_number}`` in a prompt becomes a fresh 0-100 value.

    Language models are poor sources of randomness (they repeat their most
    likely answer), so a prompt that must vary can take entropy from here.
    """
    return prompt.replace("{random_number}", str(random.randint(0, 100)))


def with_sensors(prompt: str, readings_text: str) -> str:
    """Put sensor readings into a prompt: at ``{sensors}`` if present, else appended."""
    if "{sensors}" in prompt:
        return prompt.replace("{sensors}", readings_text)
    return f"{prompt}\n\nCurrent sensor readings:\n{readings_text}"
