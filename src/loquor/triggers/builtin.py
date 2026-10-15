"""Built-in triggers.

- :class:`Console` — prints the input (a template, or JSON). The simplest
  possible actuator: show a human.
- :class:`Command` — runs a shell command with the input as JSON on stdin.
  The universal adapter: any actuator with a CLI or a script becomes a trigger.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loquor.triggers.base import Trigger, TriggerError


class Console(Trigger):
    def __init__(self, name: str = "print", description: str = "Show a message to the person at this device.",
                 input_schema: dict[str, Any] | None = None, template: str | None = None):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
        }
        self.template = template  # e.g. "[{level}] {text}"; None = text or JSON

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        if self.template:
            line = self.template.format(**input)
        elif set(input) == {"text"}:
            line = str(input["text"])
        else:
            line = json.dumps(input, ensure_ascii=False, indent=2)
        print(line, flush=True)
        return {"printed": len(line)}


class Command(Trigger):
    def __init__(self, name: str, command: str, description: str = "",
                 input_schema: dict[str, Any] | None = None, timeout: float = 30.0):
        self.name = name
        self.command = command
        self.description = description or f"runs `{command}` with the input as JSON on stdin"
        self.input_schema = input_schema or {"type": "object"}
        self.timeout = timeout

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_shell(
            self.command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(input).encode()), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TriggerError(f"timed out after {self.timeout}s")
        if proc.returncode != 0:
            raise TriggerError(f"exit {proc.returncode}: {err.decode(errors='replace').strip()[:200]}")
        text = out.decode(errors="replace").strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except ValueError:
            return {"output": text}
