"""The agent: an orchestrator with no business logic of its own.

- :class:`Agent` wires a transport, a reasoner and a list of skills
- :class:`PeerDirectory` remembers who has been heard
- :class:`RequestTracker` matches responses to requests and makes answers idempotent
- :class:`Skill` is where business logic lives; a :class:`Context` is what it gets
"""

from loquor.agent.agent import Agent
from loquor.agent.context import Context
from loquor.agent.directory import Peer, PeerDirectory
from loquor.agent.skill import Skill, fill_placeholders
from loquor.agent.tracker import RequestError, RequestTracker

__all__ = [
    "Agent",
    "Context",
    "Peer",
    "PeerDirectory",
    "RequestError",
    "RequestTracker",
    "Skill",
    "fill_placeholders",
]
