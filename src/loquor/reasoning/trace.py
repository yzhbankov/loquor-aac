"""See what the LLM was asked and what it answered.

``TracingReasoner`` wraps any reasoner. Every call is appended as one JSON
line to a trace file (``~/.loquor/run/<agent>.trace.jsonl``), kept in a
small in-memory ring for ``loquor status``, and summarised in the log::

    LLM  2.3 s  "Another agent asks: What is the weather…" -> "Tomorrow will be…"

When an agent decides something surprising, the trace file holds the
exact system prompt, prompt, schema and response of every call, in order.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from loquor.reasoning.base import Reasoner

log = logging.getLogger("loquor")

KEEP = 20  # calls remembered in memory
SNIPPET = 70  # characters of prompt/response in the log line


class TracingReasoner(Reasoner):
    def __init__(self, inner: Reasoner, path: Path | None = None, *, keep: int = KEEP):
        self.inner = inner
        self.path = path
        self.name = inner.name
        self.calls: deque[dict[str, Any]] = deque(maxlen=keep)
        self.count = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")  # one run, one trace

    @property
    def model(self) -> str | None:
        return getattr(self.inner, "model", None)

    async def complete(self, system, prompt, *, temperature=None, schema=None) -> str:
        self.count += 1
        record: dict[str, Any] = {
            "n": self.count, "at": time.time(), "model": self.model,
            "temperature": temperature, "system": system, "prompt": prompt, "schema": schema,
        }
        started = time.monotonic()
        try:
            response = await self.inner.complete(system, prompt, temperature=temperature, schema=schema)
        except Exception as exc:
            record.update(seconds=round(time.monotonic() - started, 2), error=f"{type(exc).__name__}: {exc}")
            self._record(record)
            log.warning("LLM  call %d failed after %.1f s: %s", self.count, record["seconds"], record["error"])
            raise
        record.update(seconds=round(time.monotonic() - started, 2), response=response)
        self._record(record)
        log.info("LLM  %.1f s  %s -> %s", record["seconds"], _snip(prompt), _snip(response))
        return response

    def _record(self, record: dict[str, Any]) -> None:
        self.calls.append(record)
        if self.path is not None:
            try:
                with self.path.open("a") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            except OSError as exc:  # a full disk must not stop the agent
                log.debug("cannot write trace %s: %s", self.path, exc)


def read_trace(path: Path, last: int | None = None) -> list[dict[str, Any]]:
    """The recorded calls, oldest first (the last ``last`` of them if given)."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    if last is not None:
        lines = lines[-last:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _snip(text: str, width: int = SNIPPET) -> str:
    text = " ".join(str(text).split())
    return json.dumps(text if len(text) <= width else text[: width - 1] + "…", ensure_ascii=False)
