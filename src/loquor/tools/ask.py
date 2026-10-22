"""``loquor ask``: talk to an agent or a node from the command line.

Joins the mesh for a moment as a silent, nameless peer with a throwaway
key, waits until the target is heard and the handshake is done, and asks:

- with a question, an agent's question capability (``ask``, or the first
  one that is not data) — its brain answers from what it reads right now;
- without one, ``sensor_report`` — a node's (or an agent's) readings, as data.

Nothing is installed or remembered; the target sees an ordinary request.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loquor.agent.agent import Agent
from loquor.agent.skills.brain import DATA_CAPABILITIES
from loquor.protocol.discovery import Peer, Visibility, slug
from loquor.protocol.identity import Identity
from loquor.protocol.transport import Transport
from loquor.reasoning import NoReasoner


def matches(peer: Peer, target: str) -> bool:
    """By name (any case), label (``soil-node-1a2b3c4d``), slug or id prefix."""
    t = target.strip().lower()
    return bool(t) and (peer.name.lower() == t or peer.label == t or slug(peer.name) == slug(target)
                        or peer.uid.startswith(t))


async def ask(target: str, question: str | None, transport: Transport, *, timeout: float = 30.0,
              beacon_interval: float = 1.0) -> dict[str, Any]:
    """``{"peer", "capability", "output"}``; raises ``LookupError`` or ``TimeoutError`` with the reason."""
    agent = Agent(name="loquor ask", description="A one-off question from the command line.", skills=[],
                  reasoner=NoReasoner(), transport=transport, identity=Identity.generate(),
                  visibility=Visibility.SILENT, beacon_interval=beacon_interval, mirror=False,
                  request_timeout=timeout)
    await agent.start()
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        peer = None
        while peer is None:
            peer = next((p for p in agent.node.peers.find() if matches(p, target)), None)
            if peer is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    seen = sorted(p.name for p in agent.node.peers.find())
                    raise TimeoutError(f"{target!r} not heard within {timeout:g} s"
                                       + (f"; heard {', '.join(seen)}" if seen else "; heard nobody (same transport?)"))
                try:
                    await asyncio.wait_for(agent.context.wait_for_peers(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
        if question:
            questions = [c for c in peer.capabilities if c not in DATA_CAPABILITIES]
            if not questions:
                raise LookupError(f"{peer.name} takes no questions (a node: {', '.join(peer.capabilities) or 'nothing'}); "
                                  "leave the question out to get its readings")
            capability = "ask" if "ask" in questions else questions[0]
            body: dict[str, Any] = {"question": question}
        else:
            if not peer.has("sensor_report"):
                raise LookupError(f"{peer.name} shares no readings (offers {', '.join(peer.capabilities) or 'nothing'})")
            capability, body = "sensor_report", {}
        remaining = max(1.0, deadline - loop.time())
        output = await agent.ask(peer, capability, body, timeout=remaining)
        return {"peer": peer, "capability": capability, "output": output}
    finally:
        await agent.stop()
