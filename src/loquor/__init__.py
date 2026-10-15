"""loquor — agent-to-agent communication for offline, local-first devices.

*loquor* (Latin): "I speak", "I talk", "I say".

The package lets independent AI-powered devices discover each other on a
local network, authenticate, exchange structured messages, and collaborate
on tasks — without internet access or a central server.
"""

from loquor.agent import Agent, PeerInfo
from loquor.messages import (
    Envelope,
    MessageType,
    TaskState,
)

__version__ = "0.2.0"

__all__ = [
    "Agent",
    "PeerInfo",
    "Envelope",
    "MessageType",
    "TaskState",
    "__version__",
]
