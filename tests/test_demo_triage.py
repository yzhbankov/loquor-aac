"""The triage demo end to end over the memory transport, with a scripted LLM.

Three chairs (sensor + a brain that answers) describe their patients; the
nurse (a brain with a goal, min_peers=3 and the triage_board trigger)
asks all of them and lets its LLM fill the board — validated against the
trigger's schema before it is shown.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from loquor import Agent, Identity, Reasoner
from loquor.agent.skills import Brain
from loquor.config import build_agent, load_profile, resolve_type
from loquor.transports import MemoryHub

DEMO = Path(__file__).parent.parent / "demo" / "triage"
PatientMonitor = resolve_type("patient_simulator.py:PatientMonitor", {}, "sensor", base_dir=DEMO)
TriageBoard = resolve_type("triage_board.py:TriageBoard", {}, "trigger", base_dir=DEMO)

CHAIR_PROMPT = "Chair monitor:\n{sensors}\nAssess the patient."
SEVERITY = [("chest", 1), ("breath", 2), ("fever", 3), ("", 4)]


class ScriptedClinician(Reasoner):
    """Chairs: a severity from the complaint keyword. Nurse: orders by the severities it reads."""

    name = "scripted"

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        if "Available triggers" in prompt:  # the nurse's trigger proposal
            found = re.findall(r"You asked (Triage Chair \d).*?\n  It answered: severity (\d)", prompt)
            patients = [{"chair": c, "reason": f"severity {s}"} for c, s in sorted(found, key=lambda f: f[1])]
            return json.dumps({"trigger": "triage_board", "reason": "board ready",
                               "input": {"patients": patients, "recommendations": ["ECG for chair 1."]}})
        if schema is not None:  # decision about a peer
            useful = "triage chair" in prompt.lower()
            return json.dumps({"useful": useful, "reason": "a chair with a patient" if useful else "no",
                               "capability": "ask" if useful else "", "question": "Status?"})
        if "asks:" in prompt:  # a chair answering, grounded in its sensor reading
            text = system.lower()
            assert "complaint=" in text  # the reading was placed at {sensors}
            severity = next(sev for key, sev in SEVERITY if key in text)
            return f"severity {severity}; see {'immediately' if severity == 1 else 'soon'}"
        return "Chest pain first, then breathing, then flu."


def chair(n, scenario, hub):
    return Agent(
        name=f"Triage Chair {n}", description="I am an automated triage chair.", identity=Identity.generate(),
        skills=[Brain(system_prompt=CHAIR_PROMPT)], reasoner=ScriptedClinician(),
        transport=hub.transport(), sensors=[PatientMonitor(scenario=scenario)], beacon_interval=0.05, request_timeout=3,
    )


def nurse(hub, **goal_kwargs):
    goal = Brain(goal="Order the patients.", system_prompt="You assist a nurse.", act=["triage_board"], **goal_kwargs)
    agent = Agent(name="Triage Nurse", description="nurse", identity=Identity.generate(), skills=[goal],
                  reasoner=ScriptedClinician(), transport=hub.transport(), triggers=[TriageBoard()],
                  beacon_interval=0.05, request_timeout=3)
    return agent, goal


async def run_all(agents, goal, timeout=5):
    for a in agents:
        await a.start()
    try:
        await asyncio.wait_for(goal.done.wait(), timeout)
    finally:
        for a in reversed(agents):
            await a.stop()


async def test_nurse_asks_every_chair_and_the_board_is_ordered(capsys):
    hub = MemoryHub()
    chairs = [chair(1, "chest_pain", hub), chair(2, "flu", hub), chair(3, "breathing", hub)]
    agent, goal = nurse(hub, min_peers=3, settle=30)
    await run_all([*chairs, agent], goal)

    assert sorted(a["name"] for a in goal.result["answers"]) == ["Triage Chair 1", "Triage Chair 2", "Triage Chair 3"]
    assert all(a["capability"] == "ask" for a in goal.result["answers"])
    assert goal.result["outcome"].startswith("Chest pain first") and goal.rounds == 1
    fired = goal.result["triggered"]
    assert fired["trigger"] == "triage_board" and fired["result"] == {"patients": 3}
    assert [p["chair"] for p in fired["input"]["patients"]] == ["Triage Chair 1", "Triage Chair 3", "Triage Chair 2"]
    out = capsys.readouterr().out
    assert "TRIAGE BOARD" in out and out.index("1. Triage Chair 1") < out.index("3. Triage Chair 2")
    assert "ECG for chair 1." in out


async def test_nurse_settles_when_fewer_chairs_turn_up():
    hub = MemoryHub()
    chairs = [chair(1, "chest_pain", hub), chair(2, "sprain", hub)]
    agent, goal = nurse(hub, min_peers=3, settle=0.3)
    await run_all([*chairs, agent], goal)
    assert len(goal.result["answers"]) == 2 and goal.result["triggered"]["result"] == {"patients": 2}


def test_profiles_build(tmp_path, monkeypatch):
    monkeypatch.setenv("LOQUOR_HOME", str(tmp_path))
    for name in ("chair_1", "chair_2", "chair_3"):
        agent = build_agent(load_profile(DEMO / f"{name}.json"), base_dir=DEMO)
        assert agent.capabilities == ["ask", "sensor_report"] and agent.sensors.names() == ["patient"]
        assert isinstance(agent.skills[0], Brain) and "{sensors}" in agent.skills[0].system_prompt
    agent = build_agent(load_profile(DEMO / "nurse_agent.json"), base_dir=DEMO)
    goal = agent.skills[0]
    assert isinstance(goal, Brain) and goal.min_peers == 3 and goal.settle == 15 and goal.act == ("triage_board",)
    assert agent.triggers.names() == ["triage_board"]
    with pytest.raises(ValueError, match="unknown scenario"):
        PatientMonitor(scenario="zombie")
