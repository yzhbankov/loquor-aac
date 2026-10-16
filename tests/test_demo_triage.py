"""The triage demo end to end over the memory transport, with a scripted LLM.

Three chairs assess their patients (sensor → LLM → structured status, alarm
for severe cases); the nurse's agent asks all of them, lets its LLM order
them, and fires the board trigger.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from loquor import Agent, Identity, Reasoner
from loquor.config import build_agent, load_profile, resolve_type
from loquor.transports import MemoryHub
from loquor.triggers import Console

DEMO = Path(__file__).parent.parent / "demo" / "triage"
PatientMonitor = resolve_type("patient_simulator.py:PatientMonitor", {}, "sensor", base_dir=DEMO)
TriageAssessment = resolve_type("triage_assessment.py:TriageAssessment", {}, "skill", base_dir=DEMO)
TriageRound = resolve_type("triage_round.py:TriageRound", {}, "skill", base_dir=DEMO)
TriageBoard = resolve_type("triage_board.py:TriageBoard", {}, "trigger", base_dir=DEMO)

SEVERITY_BY_COMPLAINT = [("chest", 1, "immediate"), ("breath", 2, "within 15 minutes"),
                         ("fever", 3, "within 1 hour"), ("", 4, "routine")]


class ScriptedClinician(Reasoner):
    """Assesses by keyword; orders by the severities it is shown."""

    name = "scripted"

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        if "Assess this patient" in prompt:
            text = prompt.lower()
            for key, severity, urgency in SEVERITY_BY_COMPLAINT:
                if key in text:
                    return json.dumps({"severity": severity, "urgency": urgency,
                                       "concern": f"suspected {key or 'minor'} problem", "summary": "ok"})
        if "Order them" in prompt:
            found = re.findall(r"^- (.+?): severity (\d)", prompt, re.MULTILINE)
            order = [{"chair": chair, "reason": f"severity {sev}"} for chair, sev in sorted(found, key=lambda f: f[1])]
            return json.dumps({"order": order, "recommendations": ["Start with the chest pain.", "Oxygen for chair 3."]})
        return "..."


def chair(n, scenario, hub, alarm):
    return Agent(
        name=f"Triage Chair {n}", description="chair", identity=Identity.generate(),
        skills=[TriageAssessment(alarm="alarm")], reasoner=ScriptedClinician(), transport=hub.transport(),
        sensors=[PatientMonitor(scenario=scenario)], triggers=[alarm], beacon_interval=0.05, request_timeout=3,
    )


async def test_nurse_collects_statuses_and_orders_patients(capsys):
    hub = MemoryHub()
    alarms = Console(name="alarm", template="ALARM {text}")
    chairs = [chair(1, "chest_pain", hub, alarms), chair(2, "flu", hub, alarms), chair(3, "breathing", hub, alarms)]
    round_ = TriageRound(expected_chairs=3, settle=1.0, ask_timeout=3)
    nurse = Agent(name="Triage Nurse", description="nurse", identity=Identity.generate(), skills=[round_],
                  reasoner=ScriptedClinician(), transport=hub.transport(), triggers=[TriageBoard()],
                  beacon_interval=0.05, request_timeout=3)
    for a in (*chairs, nurse):
        await a.start()
    try:
        await asyncio.wait_for(round_.done.wait(), 5)
    finally:
        for a in (nurse, *chairs):
            await a.stop()

    patients = round_.result["patients"]
    assert [(p["chair"], p["priority"], p["severity"]) for p in patients] == [
        ("Triage Chair 1", 1, 1), ("Triage Chair 3", 2, 2), ("Triage Chair 2", 3, 3)]
    assert patients[0]["urgency"] == "immediate" and "chest" in patients[0]["concern"]
    assert round_.result["recommendations"] == ["Start with the chest pain.", "Oxygen for chair 3."]
    # Each chair assessed its own patient from its own sensor...
    assert all(s.last and s.last["vitals"]["spo2_percent"] for c in chairs for s in c.skills[:1])
    out = capsys.readouterr().out
    # ...the severe ones fired their alarm directly, and the nurse's board was shown.
    assert out.count("ALARM Triage Chair") == 2 and "ALARM Triage Chair 1: severity 1" in out
    assert "TRIAGE BOARD" in out and out.index("1. 🔴 Triage Chair 1") < out.index("3. 🟡 Triage Chair 2")


async def test_ranking_keeps_every_patient_even_if_the_model_forgets_one():
    class Forgetful(Reasoner):
        name = "forgetful"

        async def complete(self, system, prompt, *, temperature=None, schema=None):
            return json.dumps({"order": [{"chair": "B", "reason": "worst"}, {"chair": "Ghost", "reason": "?"}],
                               "recommendations": ["r1", "r2", "r3", "r4", "r5"]})

    class Ctx:
        reasoner = Forgetful()

    statuses = [
        {"chair": "A", "severity": 2, "urgency": "within 15 minutes", "concern": "a"},
        {"chair": "B", "severity": 1, "urgency": "immediate", "concern": "b"},
        {"chair": "C", "severity": 4, "urgency": "routine", "concern": "c"},
    ]
    board = await TriageRound()._rank(statuses, Ctx())
    assert [(p["chair"], p["priority"]) for p in board["patients"]] == [("B", 1), ("A", 2), ("C", 3)]
    assert board["patients"][1]["reason"].startswith("not ranked by the model")
    assert len(board["recommendations"]) == 4  # capped


def test_assessment_parser_clamps_garbage():
    from importlib import import_module
    parse = TriageAssessment.__module__ and __import__("sys").modules[TriageAssessment.__module__]._parse
    assert parse("not json") == {"severity": 3, "urgency": "within 1 hour", "concern": "unclear; needs a clinician", "summary": ""}
    assert parse(json.dumps({"severity": 9, "urgency": "now", "concern": "x"}))["severity"] == 5
    assert parse(json.dumps({"severity": 0}))["urgency"] == "immediate"


def test_profiles_build(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path))
    for name in ("chair_1", "chair_2", "chair_3", "nurse_agent"):
        agent = build_agent(load_profile(DEMO / f"{name}.json"), base_dir=DEMO)
        if name.startswith("chair"):
            assert agent.capabilities == ["patient_status", "sensor_report"] and agent.triggers.names() == ["alarm"]
            assert agent.triggers.get("alarm").template.startswith("\n🚨")
        else:
            assert agent.triggers.names() == ["triage_board"] and agent.skills[0].expected_chairs == 3
    with pytest.raises(ValueError, match="unknown scenario"):
        PatientMonitor(scenario="zombie")
