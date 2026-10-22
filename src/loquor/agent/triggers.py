"""``ctx.triggers``: the agent's triggers, and the gate between the LLM and them.

Two steps, on purpose:

- ``propose()`` asks the reasoner which trigger to fire with what input, as
  JSON constrained to the allowed names; then validates the input against
  that trigger's ``input_schema``. Anything unknown or invalid becomes
  "no action" with the reason logged.
- ``fire()`` validates again and runs the trigger.

A skill may skip the LLM and call ``fire()`` directly; the LLM can never
skip the validation.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from loquor.reasoning.base import Reasoner
from loquor.triggers.base import Trigger, TriggerError

log = logging.getLogger("loquor")

NONE_WORDS = {None, "", "none", "null", "no", "nothing"}


@dataclass
class Proposal:
    trigger: str | None  # None = do nothing
    input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __bool__(self) -> bool:
        return self.trigger is not None


class TriggerHub:
    def __init__(self, triggers: Iterable[Trigger] = (), reasoner: Reasoner | None = None):
        self._triggers: dict[str, Trigger] = {}
        for trigger in triggers:
            if not trigger.name:
                raise ValueError(f"trigger {type(trigger).__name__} has no name")
            if trigger.name in self._triggers:
                raise ValueError(f"two triggers named {trigger.name!r}")
            self._triggers[trigger.name] = trigger
        self.reasoner = reasoner
        self.history: deque[dict[str, Any]] = deque(maxlen=20)  # last fires, for `loquor status`

    def names(self) -> list[str]:
        return list(self._triggers)

    def local_names(self) -> list[str]:
        """This device's own triggers, without the peers' mirrored here."""
        return [n for n, t in self._triggers.items() if not getattr(t, "remote", False)]

    def add(self, trigger: Trigger) -> None:
        """Register a trigger at runtime (a peer's, discovered on the mesh)."""
        if not trigger.name:
            raise ValueError(f"trigger {type(trigger).__name__} has no name")
        if trigger.name in self._triggers:
            raise ValueError(f"two triggers named {trigger.name!r}")
        self._triggers[trigger.name] = trigger

    def remove(self, name: str) -> None:
        self._triggers.pop(name, None)

    def __contains__(self, name: object) -> bool:
        return name in self._triggers

    def __len__(self) -> int:
        return len(self._triggers)

    def get(self, name: str) -> Trigger:
        return self._triggers[name]

    def describe(self, names: Iterable[str] | None = None) -> str:
        """The catalog the LLM reads: name, what it does, what input it takes."""
        lines = []
        for name in names if names is not None else self.names():
            t = self._triggers[name]
            lines.append(f"- {t.name}: {t.description}\n  input schema: {json.dumps(t.input_schema)}")
        return "\n".join(lines) or "(no triggers)"

    def validate(self, name: str, input: dict[str, Any]) -> str | None:
        """None if ``input`` matches the trigger's schema, else the problem."""
        try:
            jsonschema.validate(input, self._triggers[name].input_schema)
            return None
        except jsonschema.ValidationError as exc:
            return exc.message

    async def propose(
        self, system: str, situation: str, allowed: Iterable[str] | None = None, *, temperature: float = 0.0
    ) -> Proposal:
        """Ask the reasoner what to do about ``situation``; validated, never trusted."""
        if self.reasoner is None:
            raise RuntimeError("no reasoner to propose with")
        names = [n for n in (allowed if allowed is not None else self.names()) if n in self._triggers]
        if not names:
            return Proposal(None, reason="no triggers available")
        # "reason" comes first: a small model that writes its reason before choosing chooses better
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "trigger": {"type": ["string", "null"], "enum": [*names, "none", None]},
                "input": {"type": "object"},
            },
            "required": ["reason", "trigger", "input"],
        }
        if len(names) == 1:  # the model can be held to the exact input schema
            schema["properties"]["input"] = self._triggers[names[0]].input_schema
        prompt = (
            f"{situation}\n\nAvailable triggers:\n{self.describe(names)}\n\n"
            'Reply in JSON with keys, in this order: "reason" (one sentence: what in the situation calls for '
            'an action, or why nothing does), '
            '"trigger" (the trigger that action needs, or "none" to do nothing), '
            '"input" (an object matching that trigger\'s input schema).'
        )
        raw = await self.reasoner.complete(system, prompt, temperature=temperature, schema=schema)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            log.warning("trigger proposal unparseable: %r", raw[:200])
            return Proposal(None, reason=f"unparseable proposal: {raw[:100]!r}")
        reason = str(data.get("reason", ""))
        name = data.get("trigger")
        if name in NONE_WORDS:
            return Proposal(None, reason=reason)
        if name not in names:
            log.warning("trigger proposal names unknown trigger %r", name)
            return Proposal(None, reason=f"unknown trigger {name!r}")
        input = data.get("input")
        if not isinstance(input, dict):
            input = {}
        problem = self.validate(name, input)
        if problem:
            log.warning("trigger proposal for %s has invalid input: %s", name, problem)
            return Proposal(None, reason=f"invalid input for {name}: {problem}")
        return Proposal(name, input, reason)

    async def fire(self, name: str, input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and run one trigger. Raises :class:`TriggerError` on any failure."""
        trigger = self._triggers.get(name)
        if trigger is None:
            raise TriggerError(f"no trigger named {name!r}")
        input = dict(input or {})
        problem = self.validate(name, input)
        if problem:
            raise TriggerError(f"invalid input for {name}: {problem}")
        entry: dict[str, Any] = {"trigger": name, "input": input, "at": time.time()}
        try:
            result = await trigger.fire(input)
        except Exception as exc:
            error = exc if isinstance(exc, TriggerError) else TriggerError(f"{name} failed: {type(exc).__name__}: {exc}")
            self.history.append({**entry, "error": str(error)})
            raise error from exc
        if not isinstance(result, dict):
            result = {}
        self.history.append({**entry, "result": result})
        log.info("TRIGGER %s %s -> %s", name, json.dumps(input, ensure_ascii=False)[:300],
                 json.dumps(result, ensure_ascii=False, default=str)[:200])
        return result
