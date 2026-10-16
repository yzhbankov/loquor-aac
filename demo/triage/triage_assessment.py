"""The chair's skill: read the patient, let the LLM assess, answer peers.

Capability ``patient_status``: a nurse's agent asks ``{}`` and gets a
structured status — vitals, complaint, and the LLM's severity (1 = most
urgent, 5 = least), urgency and one-line concern. If the case is severe,
the chair also fires its ``alarm`` trigger directly (no LLM proposal
needed: the decision was already made in the assessment).

    {"type": "triage_assessment.py:TriageAssessment", "alarm": "alarm"}
"""

from __future__ import annotations

import json

from loquor import Skill, TriggerError

URGENCIES = ["immediate", "within 15 minutes", "within 1 hour", "routine"]

ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "integer", "minimum": 1, "maximum": 5},
        "urgency": {"type": "string", "enum": URGENCIES},
        "concern": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["severity", "urgency", "concern", "summary"],
}

DEFAULT_PROMPT = (
    "You are the triage assessment running inside an automated emergency triage chair. "
    "You see the seated patient's vitals and what they said about how they feel. "
    "Assess like an experienced triage nurse using a 1-5 severity scale "
    "(1 = life-threatening, see immediately; 2 = emergent, within 15 minutes; "
    "3 = urgent, within an hour; 4-5 = less urgent, routine). Be concise and clinical."
)


class TriageAssessment(Skill):
    capability = "patient_status"
    description = "Vitals, the patient's complaint, and a triage assessment: severity 1 (most urgent) to 5."
    input_schema = {"type": "object"}
    output_schema = {
        "type": "object",
        "properties": {
            "chair": {"type": "string"},
            "vitals": {"type": "object"},
            "complaint": {"type": "string"},
            "severity": {"type": "integer"},
            "urgency": {"type": "string"},
            "concern": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["chair", "severity", "urgency", "concern"],
    }
    sensors = ("patient",)

    def __init__(self, system_prompt: str = DEFAULT_PROMPT, alarm: str | None = None, alarm_at: int = 2):
        self.system_prompt = system_prompt
        self.alarm = alarm
        self.alarm_at = alarm_at  # fire the alarm when severity <= this
        self.triggers = (alarm,) if alarm else ()
        self.last: dict | None = None

    async def handle(self, input, ctx):
        reading = await ctx.sensors.read("patient")
        if not reading.ok:
            raise ValueError(f"patient monitor unavailable: {reading.error}")
        vitals = {k: v for k, v in reading.value.items() if k != "complaint"}
        complaint = str(reading.value.get("complaint", ""))

        raw = await ctx.reasoner.complete(
            system=self.system_prompt,
            prompt=(
                f"Patient seated in {ctx.name}.\n"
                f"Vitals: {json.dumps(vitals)}\n"
                f"The patient says: \"{complaint}\"\n\n"
                "Assess this patient. Reply in JSON with keys: "
                '"severity" (integer 1-5, 1 most urgent), '
                f'"urgency" (one of: {", ".join(URGENCIES)}), '
                '"concern" (one line: the main suspected problem), '
                '"summary" (one sentence for the nurse).'
            ),
            temperature=0,
            schema=ASSESSMENT_SCHEMA,
        )
        assessment = _parse(raw)
        status = {"chair": ctx.name, "vitals": vitals, "complaint": complaint, **assessment}
        self.last = status
        ctx.log("assessed: severity %d, %s — %s", status["severity"], status["urgency"], status["concern"])

        if self.alarm and status["severity"] <= self.alarm_at:
            try:
                await ctx.triggers.fire(self.alarm, {"text": f"{ctx.name}: severity {status['severity']} — {status['concern']}"})
            except TriggerError as exc:
                ctx.log("alarm failed: %s", exc)
        return status


def _parse(raw: str) -> dict:
    """Clamp whatever the model produced into a valid assessment."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        severity = min(5, max(1, int(data.get("severity", 3))))
    except (TypeError, ValueError):
        severity = 3
    urgency = str(data.get("urgency", ""))
    if urgency not in URGENCIES:
        urgency = URGENCIES[min(severity, 4) - 1]
    return {
        "severity": severity,
        "urgency": urgency,
        "concern": str(data.get("concern", "")).strip() or "unclear; needs a clinician",
        "summary": str(data.get("summary", "")).strip(),
    }
