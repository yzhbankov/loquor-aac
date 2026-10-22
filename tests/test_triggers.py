"""Triggers: the contract, the built-ins, and the LLM gate (propose / fire)."""

import json

import pytest

from loquor.agent.triggers import Proposal, TriggerHub
from loquor.reasoning import Reasoner
from loquor.triggers import Command, Console, FunctionTrigger, Trigger, TriggerError


class Relay(Trigger):
    name = "relay"
    description = "Switch the pump relay."
    input_schema = {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}

    def __init__(self):
        self.states = []

    async def fire(self, input):
        self.states.append(input["on"])
        return {"state": input["on"]}


class Faulty(Trigger):
    name = "faulty"

    async def fire(self, input):
        raise OSError("bus error")


class Scripted(Reasoner):
    """Returns whatever JSON the test wants; records the schema it was given."""

    name = "scripted"

    def __init__(self, reply):
        self.reply = reply
        self.schemas = []

    async def complete(self, system, prompt, *, temperature=None, schema=None):
        self.schemas.append(schema)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


async def test_console_prints_text_json_or_template(capsys):
    assert await Console().fire({"text": "hi"}) == {"printed": 2}
    await Console(name="alert", input_schema={"type": "object"}).fire({"level": "high", "text": "x"})
    await Console(name="t", template="[{level}] {text}", input_schema={"type": "object"}).fire({"level": "L", "text": "T"})
    out = capsys.readouterr().out
    assert "hi\n" in out and '"level": "high"' in out and "[L] T" in out


async def test_function_trigger_and_command_trigger():
    calls = []

    async def beep(input):
        """Sound the buzzer."""
        calls.append(input)

    t = FunctionTrigger(beep, name="beep")
    assert t.description == "Sound the buzzer." and await t.fire({"ms": 100}) == {} and calls == [{"ms": 100}]

    echo = Command("echo", "cat")  # echoes the JSON input back
    assert await echo.fire({"a": 1}) == {"a": 1}
    assert await Command("w", "wc -c | tr -d ' '").fire({}) == {"output": 2.0}
    with pytest.raises(TriggerError, match="exit 3"):
        await Command("f", "exit 3").fire({})


async def test_hub_fire_validates_and_wraps_failures():
    relay = Relay()
    hub = TriggerHub([relay, Faulty()])
    assert hub.names() == ["relay", "faulty"] and "relay" in hub
    assert await hub.fire("relay", {"on": True}) == {"state": True} and relay.states == [True]
    with pytest.raises(TriggerError, match="invalid input for relay"):
        await hub.fire("relay", {"on": "yes"})
    with pytest.raises(TriggerError, match="no trigger named"):
        await hub.fire("laser", {})
    with pytest.raises(TriggerError, match="faulty failed: OSError: bus error"):
        await hub.fire("faulty", {})
    assert relay.states == [True]  # the invalid call never reached the trigger
    with pytest.raises(ValueError, match="two triggers named"):
        TriggerHub([Relay(), Relay()])


async def test_hub_propose_accepts_only_valid_proposals():
    relay = Relay()

    async def propose(reply, allowed=None):
        reasoner = Scripted(reply)
        hub = TriggerHub([relay, Console()], reasoner)
        return await hub.propose("sys", "the tank is empty", allowed), reasoner

    p, r = await propose({"trigger": "relay", "input": {"on": True}, "reason": "fill it"}, ["relay"])
    assert p == Proposal("relay", {"on": True}, "fill it") and bool(p)
    assert r.schemas[0]["properties"]["trigger"]["enum"] == ["relay", "none", None]
    assert r.schemas[0]["properties"]["input"] == Relay.input_schema  # single trigger: exact schema

    p, r = await propose({"trigger": None, "input": {}, "reason": "nothing to do"})
    assert not p and p.reason == "nothing to do"
    assert r.schemas[0]["properties"]["input"] == {"type": "object"}  # several triggers: generic

    p, _ = await propose({"trigger": "laser", "input": {}, "reason": "zap"})
    assert not p and "unknown trigger" in p.reason
    p, _ = await propose({"trigger": "relay", "input": {"on": "yes"}, "reason": "x"})
    assert not p and "invalid input" in p.reason
    p, _ = await propose({"trigger": "relay", "input": {"on": True}, "reason": "x"}, allowed=["print"])
    assert not p and "unknown trigger" in p.reason  # not in the allowed subset
    p, _ = await propose("not json at all")
    assert not p and "unparseable" in p.reason
    p, _ = await propose({"trigger": "none", "input": {}, "reason": "r"})
    assert not p
    assert relay.states == []  # proposing never fires

    with pytest.raises(RuntimeError, match="no reasoner"):
        await TriggerHub([relay]).propose("s", "x")
    assert (await TriggerHub([relay], Scripted({})).propose("s", "x", allowed=["nope"])).reason == "no triggers available"
