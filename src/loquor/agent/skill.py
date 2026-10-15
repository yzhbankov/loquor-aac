"""Skill interface: where business logic lives.

A skill can do either or both of:

- **answer** requests for its ``capability`` (``handle``). The capability is
  advertised to other agents, and requests naming it are dispatched here.
- **act** on its own (``run``): a background loop that reads sensors, watches
  peers, asks them things. A skill with no capability only runs.

Both receive a :class:`~loquor.agent.context.Context`: the reasoner, the
peers heard so far, and ``ask()``.
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
