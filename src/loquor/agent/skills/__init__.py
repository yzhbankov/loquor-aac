"""Built-in skills, selectable by name in a profile (``"type": "llm_answer"``)."""

from loquor.agent.skills.collect import Collect
from loquor.agent.skills.goal import Goal
from loquor.agent.skills.llm_answer import LlmAnswer
from loquor.agent.skills.offer_triggers import OfferTriggers
from loquor.agent.skills.sensor_report import SensorReport

BUILTIN_SKILLS = {
    "llm_answer": LlmAnswer,
    "goal": Goal,
    "collect": Collect,
    "sensor_report": SensorReport,
    "offer_triggers": OfferTriggers,
}

__all__ = ["BUILTIN_SKILLS", "Collect", "Goal", "LlmAnswer", "OfferTriggers", "SensorReport"]
