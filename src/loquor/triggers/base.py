"""The trigger contract.

Rules for a trigger:

1. ``name``, ``description`` and ``input_schema`` are the contract. The
   description tells the LLM what the trigger does and when to use it; the
   schema is what the LLM's proposal is held to — the agent validates before
   ``fire()`` is ever called, so ``fire()`` can trust its input.
2. ``fire(input)`` does one thing and returns a small JSON-safe dict (may be
   empty). Raise :class:`TriggerError` (or anything) on failure; the agent
   logs it and reports it, nothing else breaks.
3. Blocking work (serial writes, subprocesses) goes through
   ``asyncio.to_thread``; bound the time.
4. Say in ``description`` if firing twice is not safe.
5. Import nothing from loquor but this module. A trigger knows nothing about
   agents, peers or LLMs.

A plain function works too::

    async def beep(input):            # or a sync def
        ...
    # profile: {"type": "my_drivers.py:beep", "name": "beep",
    #           "description": "Sound the buzzer.", "input_schema": {"type": "object"}}
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


class TriggerError(Exception):
    """The trigger could not do its job."""


class Trigger:
    name: str = ""
    description: str = ""  # what it does and when to use it; the LLM reads this
    input_schema: dict[str, Any] = {"type": "object"}

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class FunctionTrigger(Trigger):
    """Adapts a function (sync or async) taking the input dict."""

    def __init__(self, fn: Callable[[dict[str, Any]], Any], name: str, description: str = "",
                 input_schema: dict[str, Any] | None = None):
        self.fn = fn
        self.name = name
        self.description = description or (inspect.getdoc(fn) or "").split("\n")[0]
        self.input_schema = input_schema or {"type": "object"}

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        result = self.fn(input)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}
