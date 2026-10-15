"""loquor — agent-to-agent communication for offline, local-first devices.

*loquor* (Latin): "I speak", "I talk", "I say".

Independent AI agents on different devices discover each other over a local
transport, exchange signed structured messages, and collaborate — with no
internet, no cloud and no central server. See docs/ARCHITECTURE.md.
"""

from loquor.agent import Agent, Context, Peer, RequestError, Skill
from loquor.protocol import Message, MessageType
from loquor.reasoning import Reasoner
from loquor.transports import Transport

__version__ = "0.3.0"

__all__ = [
    "Agent",
    "Context",
    "Message",
    "MessageType",
    "Peer",
    "Reasoner",
    "RequestError",
    "Skill",
    "Transport",
    "__version__",
]
