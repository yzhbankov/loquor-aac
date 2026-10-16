"""loquor — agent-to-agent communication for offline, local-first devices.

*loquor* (Latin): "I speak", "I talk", "I say".

Independent AI agents on different devices discover each other over a local
transport, exchange signed and encrypted messages (AMP v1), and collaborate
— with no internet, no cloud and no central server. See docs/ARCHITECTURE.md.
"""

from loquor.agent import Agent, Context, RequestError, Skill
from loquor.protocol import Envelope, Identity, Peer, Transport, Trust, Visibility
from loquor.reasoning import Reasoner
from loquor.sensors import Reading, Sensor
from loquor.triggers import Trigger, TriggerError

__version__ = "0.5.0"

__all__ = [
    "Agent",
    "Context",
    "Envelope",
    "Identity",
    "Peer",
    "Reading",
    "Reasoner",
    "Sensor",
    "RequestError",
    "Skill",
    "Transport",
    "Trigger",
    "TriggerError",
    "Trust",
    "Visibility",
    "__version__",
]
