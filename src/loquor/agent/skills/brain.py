"""The brain: one skill for a device that thinks.

A profile's ``"brain"`` block becomes this skill. It does two things, both
optional:

- **answers** questions from other agents (capability ``ask``, input
  ``{"question": str}`` → ``{"answer": str}``), grounded in the readings of
  its sensors — its own and those that nodes in range share — read at the
  moment the question arrives;
- **pursues a goal**: it waits until enough peers are in range, reads the
  sensors it needs (a node is asked only now, on demand), asks the agents
  in range that can help (its LLM decides once per agent whether and what
  to ask), thinks once over all of it, and either fires one of the
  triggers it may use — its own or a node's (``tank-node:valve``),
  validated on both sides — or writes an assessment. With ``every`` it
  does that every ``every`` seconds; without, once.

Nodes need no deciding: their sensors are data, listed in their card,
mirrored into this agent's sensor hub as ``<node>:<sensor>``. Only agents
(peers that offer more than data) cost an LLM call to consider.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loquor.agent.context import Context
from loquor.agent.remote import is_remote_name
from loquor.agent.skill import Skill, fill_placeholders, with_sensors
from loquor.agent.skills.goal import DECISION_SCHEMA
from loquor.agent.tracker import RequestError
from loquor.protocol.discovery import Peer
from loquor.sensors.base import Reading
from loquor.triggers.base import TriggerError

#: Capabilities that carry data or actions, not answers: a peer offering only
#: these is a node, read through its mirrored sensors, never asked questions.
DATA_CAPABILITIES = {"sensor_report", "fire_trigger", "collected_readings"}


class Brain(Skill):
    input_schema = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
    output_schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

    def __init__(self, goal: str = "", system_prompt: str = "", every: float | None = None,
                 act: list[str] | None = None, answer: bool = True, sensors: list[str] | None = None,
                 peers: list[str] | None = None, ask_agents: bool = True, min_peers: int | None = None,
                 settle: float = 0.0, temperature: float = 0.2, capability: str = "ask",
                 description: str = "", max_chars: int = 3000):
        self.goal = goal
        self.system_prompt = system_prompt or "You are an agent on a device in an offline local network."
        self.every = every
        self.act = None if act is None else tuple(act)  # None = every trigger it can reach; () = never act
        self.read = None if sensors is None else tuple(sensors)  # None = its own and the nodes' in range
        self.peers = tuple(peers or ())  # names or ids of the peers to use; () = all
        self.ask_agents = ask_agents
        self.min_peers = min_peers
        self.settle = settle
        self.temperature = temperature
        self.max_chars = max_chars
        self.capability = capability if answer else ""
        self.description = description or "Ask a question in plain language; the answer comes from this device's readings."
        # checked at startup: the local ones it names (a node's appear at runtime)
        self.sensors = tuple(n for n in self.read or () if not is_remote_name(n))
        self.triggers = tuple(n for n in self.act or () if not is_remote_name(n))

        self.decisions: dict[str, dict[str, Any]] = {}  # agent uid -> {useful, reason, capability, question}
        self.answers: dict[str, dict[str, Any]] = {}  # agent uid -> what it answered
        self.rounds = 0
        self.assessment: str | None = None
        self.result: dict[str, Any] | None = None
        self.done = asyncio.Event()  # set when a goal without `every` is complete
        self.thought = asyncio.Event()  # set after every round, for tests and tools

    # -------------------------------------------------------------- answer

    async def handle(self, input: dict[str, Any], ctx: Context) -> dict[str, Any]:
        question = str(input.get("question") or "").strip()
        if not question:
            raise ValueError("input.question is required")
        readings = await ctx.sensors.readings(self.sensor_names(ctx))
        system = with_sensors(fill_placeholders(self.system_prompt), ctx.sensors.describe(readings))
        if self.assessment:
            system += f"\n\nYour latest assessment: {self.assessment}"
        answer = await ctx.reasoner.complete(system=system, prompt=f"Another agent asks: {question}",
                                             temperature=self.temperature)
        return {"answer": answer.strip()[: self.max_chars]}

    # ---------------------------------------------------------------- loop

    async def run(self, ctx: Context) -> None:
        if not self.goal:
            return
        need = self.min_peers if self.min_peers is not None else (0 if self.every and ctx.sensors.local_names() else 1)
        sources: set[str] = set()
        last_new = time.monotonic()
        refresh = True
        while True:
            await self._consult(ctx, refresh=refresh and self.rounds > 0)
            refresh = False
            now = self._sources(ctx)
            if now - sources:
                last_new = time.monotonic()
            sources = now
            waited = time.monotonic() - last_new
            ready = len(sources) >= need or (self.settle > 0 and sources and waited >= self.settle)
            if not ready:
                timeout = self.settle - waited if self.settle > 0 and sources else self.every
                try:
                    await asyncio.wait_for(ctx.wait_for_peers(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
                continue
            self.rounds += 1
            await self._think(ctx)
            self.thought.set()
            if not self.every:
                self.done.set()
                return
            await asyncio.sleep(self.every)
            refresh = True

    def _accepts(self, peer: Peer) -> bool:
        return not self.peers or peer.name in self.peers or peer.uid in self.peers

    def _sources(self, ctx: Context) -> set[str]:
        """The peers whose data this round can use: nodes with sensors read here, agents that answered.

        An agent still to be considered, or found useful but not yet answered, is not counted until it has.
        """
        names = set(self.sensor_names(ctx))
        pending = set()
        if self.ask_agents:
            for peer in ctx.peers():
                decision = self.decisions.get(peer.uid)
                if self._accepts(peer) and self._questions(peer) and (
                        decision is None or (decision["useful"] and peer.uid not in self.answers)):
                    pending.add(peer.uid)
        nodes = {peer.uid for name, peer in ctx.remote_sensors().items() if name in names}
        return (nodes - pending) | set(self.answers)

    def sensor_names(self, ctx: Context) -> list[str]:
        """What it reads: the named sensors, or its own plus those of the accepted nodes in range.

        An agent's shared sensors are read only when named: an agent is asked, a node is read.
        """
        remote = ctx.remote_sensors()
        if self.read is not None:
            return [n for n in self.read if n not in remote or self._accepts(remote[n])]
        return [n for n in ctx.sensors.names()
                if n not in remote or (self._accepts(remote[n]) and not self._questions(remote[n]))]

    def allowed_triggers(self, ctx: Context) -> list[str]:
        remote = ctx.remote_triggers()
        names = ctx.triggers.names() if self.act is None else [n for n in self.act if n in ctx.triggers]
        return [n for n in names if n not in remote or self._accepts(remote[n])]

    # ------------------------------------------------------------- consult

    async def _consult(self, ctx: Context, *, refresh: bool) -> None:
        """Ask the agents in range that can help; each is considered once, asked again only to refresh."""
        if not self.ask_agents:
            return
        for peer in ctx.peers():
            if not self._accepts(peer) or not self._questions(peer):
                continue
            decision = self.decisions.get(peer.uid)
            if decision is None:
                decision = await self._decide(peer, ctx)
                if decision is None:
                    continue  # unparseable: reconsider on its next beacon
                self.decisions[peer.uid] = decision
                ctx.log("reasoning about %s: useful=%s — %s", peer.label, decision["useful"], decision["reason"])
            if not decision["useful"] or (peer.uid in self.answers and not refresh):
                continue
            capability = decision["capability"] if decision["capability"] in self._questions(peer) else self._questions(peer)[0]
            if not decision["question"]:  # small models often decide well but leave the question empty
                decision["question"] = await self._write_question(peer, ctx)
            question = decision["question"]
            try:
                output = await ctx.ask(peer, capability, {"question": question})
            except TimeoutError:
                ctx.log("%s did not answer; will ask again", peer.label)
                continue
            except RequestError as exc:
                ctx.log("%s could not help: %s", peer.label, exc)
                self.decisions[peer.uid] = {**decision, "useful": False}
                continue
            self.answers[peer.uid] = {"name": peer.name, "description": peer.description, "capability": capability,
                                      "question": question, "answer": str(output.get("answer", output))}

    @staticmethod
    def _questions(peer: Peer) -> list[str]:
        """The capabilities of a peer that take a question (an agent's, not a node's)."""
        return [c for c in peer.capabilities if c not in DATA_CAPABILITIES]

    async def _decide(self, peer: Peer, ctx: Context) -> dict[str, Any] | None:
        shared = [n for n, p in ctx.remote_sensors().items() if p.uid == peer.uid]
        raw = await ctx.reasoner.complete(
            system=f"{self.system_prompt}\n\nYour goal: {self.goal}",
            prompt=(
                "Another agent on the local network advertised itself:\n"
                f"- name: {peer.name}\n"
                f"- description: {peer.description}\n"
                f"- capabilities: {', '.join(self._questions(peer))}\n"
                + (f"- sensors it shares (you will read them anyway): {', '.join(shared)}\n" if shared else "")
                + "\nCan this agent give you information you need for your goal? Reply in JSON with keys:\n"
                '- "useful": true or false\n'
                '- "reason": one sentence explaining your decision\n'
                '- "capability": if useful, which of its capabilities you will use; otherwise ""\n'
                '- "question": if useful, the short question you will send to this agent to get the missing '
                'information (addressed to it, about what it provides — not your own goal); otherwise ""'
            ),
            temperature=0,
            schema=DECISION_SCHEMA,
        )
        try:
            decision = json.loads(raw)
            return {
                "useful": bool(decision["useful"]),
                "reason": str(decision.get("reason", "")),
                "capability": str(decision.get("capability", "")).strip(),
                "question": str(decision.get("question", "")).strip(),
            }
        except (ValueError, KeyError, TypeError):
            ctx.log("unparseable decision from reasoner: %r", raw[:200])
            return None

    async def _write_question(self, peer: Peer, ctx: Context) -> str:
        question = await ctx.reasoner.complete(
            system=f"{self.system_prompt}\n\nYour goal: {self.goal}",
            prompt=(f"You will ask {peer.name} ({peer.description}) for the information you are missing. "
                    "Write only that one short question, addressed to it, nothing else."),
            temperature=0,
        )
        return question.strip().strip('"') or "What can you tell me?"

    # --------------------------------------------------------------- think

    async def _think(self, ctx: Context) -> None:
        readings = await ctx.sensors.readings(self.sensor_names(ctx))
        situation = f"Your goal: {self.goal}\n\n{self.situation(readings, ctx)}"
        system = fill_placeholders(self.system_prompt).replace("{sensors}", "(see below)")
        allowed = self.allowed_triggers(ctx)
        triggered = None
        conclusion = None
        if allowed and not self.every:  # once: conclude in prose first, then act on the conclusion
            conclusion = (await ctx.reasoner.complete(
                system=system, prompt=situation + "\n\nNow complete your goal: answer it in two or three short sentences.",
                temperature=self.temperature,
            )).strip()
            ctx.log("concluded: %s", conclusion)
            situation += f"\n\nYour conclusion: {conclusion}"
        if allowed:
            proposal = await ctx.triggers.propose(
                system=system,
                situation=situation + "\n\nDecide whether to act now with one of the available triggers, "
                                      "filling its input from what you know, or choose none if nothing is needed.",
                allowed=allowed, temperature=self.temperature,
            )
            self.assessment = conclusion or proposal.reason
            if proposal:
                ctx.log("round %d: firing %s — %s", self.rounds, proposal.trigger, proposal.reason)
                triggered = {"trigger": proposal.trigger, "input": proposal.input}
                try:
                    triggered["result"] = await ctx.triggers.fire(proposal.trigger, proposal.input)
                except TriggerError as exc:
                    triggered["error"] = str(exc)
                    ctx.log("trigger failed: %s", exc)
            else:
                ctx.log("round %d: no action — %s", self.rounds, proposal.reason)
        else:
            self.assessment = (await ctx.reasoner.complete(
                system=system, prompt=situation + "\n\nNow complete your goal: answer it in two or three short sentences.",
                temperature=self.temperature,
            )).strip()
            ctx.log("round %d: %s", self.rounds, self.assessment)
        self.result = {"goal": self.goal, "round": self.rounds, "outcome": self.assessment, "triggered": triggered,
                       "answers": list(self.answers.values())}

    def situation(self, readings: list[Reading], ctx: Context) -> str:
        """Prompt-ready: own readings, nodes' readings, agents' answers."""
        remote = ctx.remote_sensors()
        own = [r.text() for r in readings if r.sensor not in remote and not is_remote_name(r.sensor)]
        nodes = [r.text() for r in readings if r.sensor in remote or is_remote_name(r.sensor)]
        parts = []
        if own:
            parts.append("Your own sensors:\n" + "\n".join(f"- {line}" for line in own))
        if nodes:
            parts.append("Sensors on devices nearby (device:sensor):\n" + "\n".join(f"- {line}" for line in nodes))
        if self.answers:
            parts.append("What other agents told you:\n" + "\n".join(
                f"- You asked {a['name']} ({a['description']}): {a['question']}\n  It answered: {a['answer']}"
                for a in self.answers.values()))
        return "\n\n".join(parts) or "(nothing measured or heard yet)"
