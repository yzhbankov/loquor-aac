"""Built-in triggers.

- :class:`Console` — prints the input (a template, or JSON). The simplest
  possible actuator: show a human.
- :class:`Command` — runs a shell command with the input as JSON on stdin.
  The universal adapter: any actuator with a CLI or a script becomes a trigger.
- :class:`File` — writes the input, formatted, to a file: sysfs controls
  (an LED's ``brightness``, a GPIO ``value``), or a file a driver watches.
- :class:`Http` — calls a URL with the input: devices with a web API (smart
  plugs, relay boards, ESP32s). Input fields fill ``{placeholders}`` in the URL.

GPIO pins and serial ports are in :mod:`loquor.triggers.hardware`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
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


class File(Trigger):
    """Writes ``template`` filled from the input to ``path``. ``{on:d}`` writes a boolean as 1/0."""

    def __init__(self, name: str, path: str, description: str = "", input_schema: dict[str, Any] | None = None,
                 template: str = "{value}", append: bool = False):
        super().__init__(name=name, description=description or f"writes to {path}",
                         input_schema=input_schema or {"type": "object", "properties": {"value": {}}, "required": ["value"]})
        self.path = Path(path).expanduser()
        self.template, self.append = template, append

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            text = self.template.format(**input)
        except (KeyError, IndexError, ValueError) as exc:
            raise TriggerError(f"template {self.template!r} does not fit the input: {exc}") from exc

        def write() -> None:
            with self.path.open("a" if self.append else "w") as f:
                f.write(text if not self.append or text.endswith("\n") else text + "\n")

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            raise TriggerError(f"cannot write {self.path}: {exc.strerror or exc}") from exc
        return {"wrote": text}


class Http(Trigger):
    """Calls ``url`` (``{field}`` placeholders filled from the input) with ``method``;
    POST/PUT/PATCH send the input as a JSON body. The JSON answer is the result."""

    def __init__(self, name: str, url: str, description: str = "", input_schema: dict[str, Any] | None = None,
                 method: str = "POST", headers: dict[str, str] | None = None, timeout: float = 10.0):
        super().__init__(name=name, description=description or f"{method} {url}",
                         input_schema=input_schema or {"type": "object"})
        self.url, self.method, self.headers, self.timeout = url, method.upper(), headers or {}, timeout

    async def fire(self, input: dict[str, Any]) -> dict[str, Any]:
        import aiohttp  # noqa: PLC0415

        try:
            url = self.url.format(**input)
        except (KeyError, IndexError, ValueError) as exc:
            raise TriggerError(f"url {self.url!r} does not fit the input: {exc}") from exc
        body = input if self.method in ("POST", "PUT", "PATCH") else None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.request(self.method, url, json=body, headers=self.headers) as response:
                    text = await response.text()
                    if response.status >= 400:
                        raise TriggerError(f"HTTP {response.status} from {url}: {text[:200]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TriggerError(f"{url}: {type(exc).__name__} {exc}".strip()) from exc
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except ValueError:
            return {"status": response.status, "output": text[:500]}
