"""Built-in skills, selectable by name in a profile (``"type": "llm_answer"``)."""

from loquor.agent.skills.goal import Goal
from loquor.agent.skills.llm_answer import LlmAnswer
from loquor.agent.skills.sensor_report import SensorReport

BUILTIN_SKILLS = {
    "llm_answer": LlmAnswer,
    "goal": Goal,
    "sensor_report": SensorReport,
}

__all__ = ["BUILTIN_SKILLS", "Goal", "LlmAnswer", "SensorReport"]
