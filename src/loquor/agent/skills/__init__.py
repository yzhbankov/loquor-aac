"""Built-in skills, selectable by name in a profile (``"type": "llm_answer"``).

Most profiles never name one: ``"brain"`` becomes a :class:`Brain`, and
``"share"`` (on by default) becomes :class:`SensorReport` and
:class:`OfferTriggers`. The others remain for profiles that want exactly
one behaviour.
"""

from loquor.agent.skills.brain import Brain
from loquor.agent.skills.collect import Collect
from loquor.agent.skills.goal import Goal
from loquor.agent.skills.llm_answer import LlmAnswer
from loquor.agent.skills.offer_triggers import OfferTriggers
from loquor.agent.skills.sensor_report import SensorReport

BUILTIN_SKILLS = {
    "brain": Brain,
    "llm_answer": LlmAnswer,
    "goal": Goal,
    "collect": Collect,
    "sensor_report": SensorReport,
    "offer_triggers": OfferTriggers,
}

__all__ = ["BUILTIN_SKILLS", "Brain", "Collect", "Goal", "LlmAnswer", "OfferTriggers", "SensorReport"]
