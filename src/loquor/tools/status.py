"""What a running agent knows, as one JSON file another process can read.

The agent writes ``~/.loquor/run/<name>.json`` every couple of seconds:
peers it sees, its latest readings, the triggers it fired, the last LLM
calls, its goals. ``loquor status`` reads it. No socket, no daemon, works
the same on every OS and under a service manager.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from loquor.agent.agent import Agent
from loquor.protocol.discovery import slug
from loquor.settings import run_dir

INTERVAL = 2.0


def status_path(name: str) -> Path:
    return run_dir() / f"{slug(name) or 'agent'}.json"


def trace_path(name: str) -> Path:
    return run_dir() / f"{slug(name) or 'agent'}.trace.jsonl"


def snapshot(agent: Agent, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything worth knowing about a running agent, JSON-safe."""
    now = time.monotonic()
    reasoner = agent.reasoner
    calls = list(getattr(reasoner, "calls", ()))
    transport = agent.transport
    data: dict[str, Any] = {
        "name": agent.name,
        "uid": agent.uid,
        "agent_id": agent.agent_id.hex(),
        "pid": os.getpid(),
        "updated_at": time.time(),
        "transport": {"type": type(transport).__name__, "where": getattr(transport, "interface", None)},
        "model": getattr(reasoner, "model", None),
        "reasoner": reasoner.name,
        "visibility": agent.node.visibility.value,
        "capabilities": agent.capabilities,
        "peers": [
            {
                "uid": p.uid,
                "label": p.label,
                "name": p.name,
                "description": p.description,
                "capabilities": p.capabilities,
                "trust": p.trust,
                "handshaken": p.handshaken,
                "last_seen_s": round(max(0.0, now - p.last_seen), 1),
            }
            for p in agent.node.peers.find(handshaken=False)
        ],
        "sensors": [
            {"sensor": name, **(_reading(agent.sensors.latest(name)) if agent.sensors.latest(name) else {"value": None, "note": "no reading yet"})}
            for name in agent.sensors.names()
        ],
        "triggers": {"names": agent.triggers.names(), "history": list(agent.triggers.history)},
        "llm": {
            "calls": getattr(reasoner, "count", None),
            "last": [
                {k: c.get(k) for k in ("n", "at", "seconds", "error")} | {
                    "prompt": _snip(c.get("prompt", "")), "response": _snip(c.get("response", ""))}
                for c in calls[-5:]
            ],
        },
        "goals": [
            {
                "goal": s.goal,
                "done": s.done.is_set(),
                "answers": len(s.answers),
                "outcome": (s.result or {}).get("outcome"),
                "triggered": (s.result or {}).get("triggered"),
            }
            for s in agent.skills if hasattr(s, "goal") and hasattr(s, "done")
        ],
        "collected": [
            {"rounds": s.rounds, "goal": s.goal or None, "assessment": s.assessment,
             "nodes": [{"label": n["label"], "age_s": round(time.time() - n["at"], 1), "readings": n["readings"]}
                       for n in s.collected.values()]}
            for s in agent.skills if hasattr(s, "collected") and hasattr(s, "rounds")
        ],
    }
    if extra:
        data.update(extra)
    return data


def _reading(reading) -> dict[str, Any]:
    out = {"value": reading.value, "unit": reading.unit, "age_s": round(reading.age, 1)}
    if reading.error:
        out["error"] = reading.error
    return out


def _snip(text: Any, width: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


class StatusWriter:
    """Writes the snapshot to ``path`` every ``interval`` seconds while the agent runs."""

    def __init__(self, agent: Agent, path: Path | None = None, *, interval: float = INTERVAL,
                 extra: dict[str, Any] | None = None):
        self.agent = agent
        self.path = path or status_path(agent.name)
        self.interval = interval
        self.extra = {"started_at": time.time(), **(extra or {})}
        self._task: asyncio.Task | None = None

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot(self.agent, extra=self.extra), ensure_ascii=False, default=str, indent=1))
        os.replace(tmp, self.path)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                self.write()
            except OSError:
                pass  # a full disk must not stop the agent
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            self.path.unlink()
        except OSError:
            pass


def running() -> list[dict[str, Any]]:
    """Status of every agent that left a file, with ``alive`` from its pid."""
    out = []
    for path in sorted(run_dir().glob("*.json")):
        data = read_status(path)
        if data is not None:
            out.append(data)
    return out


def read_status(target: str | Path) -> dict[str, Any] | None:
    """Status by agent name, profile path or status file path; None if there is none."""
    path = Path(target)
    if path.suffix == ".json" and path.exists() and path.parent != run_dir():
        try:  # a profile: find its agent by name
            name = json.loads(path.read_text()).get("name", "")
        except (ValueError, AttributeError):
            name = path.stem
        path = status_path(name)
    elif not path.exists():
        path = status_path(str(target))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return None
    data["alive"] = _alive(data.get("pid")) and time.time() - float(data.get("updated_at", 0)) < 5 * INTERVAL
    data["status_file"] = str(path)
    return data


def _alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError, PermissionError):
        return False


def format_status(data: dict[str, Any]) -> str:
    """A readable page for the terminal."""
    lines = []
    state = "running" if data.get("alive") else "not running (stale status file)"
    lines.append(f"{data.get('uid')}  {state}  pid {data.get('pid')}")
    lines.append(f"  model      : {data.get('model') or data.get('reasoner')}")
    t = data.get("transport") or {}
    lines.append(f"  transport  : {t.get('type')} ({t.get('where')})  visibility {data.get('visibility')}")
    lines.append(f"  offers     : {', '.join(data.get('capabilities') or []) or 'nothing'}")
    peers = data.get("peers") or []
    lines.append(f"  peers      : {len(peers)}")
    for p in peers:
        hs = "handshaken" if p.get("handshaken") else "heard only"
        lines.append(f"    - {p.get('label')}  {hs}, trust {p.get('trust')}, seen {p.get('last_seen_s')} s ago: "
                     f"{', '.join(p.get('capabilities') or []) or '?'}")
    sensors = data.get("sensors") or []
    lines.append(f"  sensors    : {len(sensors)}")
    for s in sensors:
        if s.get("error"):
            lines.append(f"    - {s['sensor']}: unavailable ({s['error']})")
        elif s.get("value") is None:
            lines.append(f"    - {s['sensor']}: {s.get('note', 'no reading')}")
        else:
            lines.append(f"    - {s['sensor']}: {s['value']} {s.get('unit', '')} ({s.get('age_s')} s ago)")
    trig = data.get("triggers") or {}
    lines.append(f"  triggers   : {', '.join(trig.get('names') or []) or 'none'}")
    for h in (trig.get("history") or [])[-5:]:
        outcome = f"error {h['error']}" if h.get("error") else f"-> {json.dumps(h.get('result'), ensure_ascii=False)}"
        lines.append(f"    - {h.get('trigger')} {json.dumps(h.get('input'), ensure_ascii=False)[:100]} {outcome}")
    llm = data.get("llm") or {}
    lines.append(f"  llm calls  : {llm.get('calls') if llm.get('calls') is not None else 'not traced'}")
    for c in llm.get("last") or []:
        tail = f"failed: {c['error']}" if c.get("error") else f"-> {c.get('response')}"
        lines.append(f"    #{c.get('n')} {c.get('seconds')} s  {c.get('prompt')}  {tail}")
    for g in data.get("goals") or []:
        state = "complete" if g.get("done") else f"in progress ({g.get('answers')} answers)"
        lines.append(f"  goal       : {g.get('goal')}  [{state}]")
        if g.get("outcome"):
            lines.append(f"    outcome  : {_snip(g['outcome'], 300)}")
    for c in data.get("collected") or []:
        lines.append(f"  collected  : {len(c.get('nodes') or [])} node(s), {c.get('rounds')} round(s)"
                     + (f", goal: {_snip(c['goal'], 80)}" if c.get("goal") else ""))
        for n in c.get("nodes") or []:
            readings = "; ".join(f"{k}={v.get('value') if isinstance(v, dict) else v} {v.get('unit', '') if isinstance(v, dict) else ''}".rstrip()
                                 for k, v in (n.get("readings") or {}).items())
            lines.append(f"    - {n.get('label')} ({n.get('age_s')} s ago): {readings}")
        if c.get("assessment"):
            lines.append(f"    assessment: {_snip(c['assessment'], 300)}")
    lines.append(f"  status file: {data.get('status_file')}")
    return "\n".join(lines)
