"""Pursue a goal with help from the peers that are heard.

For each peer, the reasoner decides once whether it can help with the goal
and what to ask it (JSON: ``useful``, ``reason``, ``capability``,
``question``). If useful, the skill asks, then reasons over the answer to
complete the goal. The goal completes once; the agent keeps advertising and
answering.

A peer whose decision cannot be parsed, or that never answers, is
reconsidered on its next advert.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loquor.agent.context import Context
from loquor.agent.directory import Peer
from loquor.agent.skill import Skill
from loquor.agent.tracker import RequestError

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "useful": {"type": "boolean"},
        "reason": {"type": "string"},
        "capability": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["useful", "reason", "capability", "question"],
}


class Goal(Skill):
    capability = ""  # pursues; answers nothing

    def __init__(self, goal: str, system_prompt: str, temperature: float = 0.2):
        self.goal = goal
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.result: dict[str, Any] | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: Context) -> None:
        considered: set[str] = set()
        while not self.done.is_set():
            for peer in ctx.peers():
                if peer.uid in considered or not peer.capabilities:
                    continue
                considered.add(peer.uid)
                if not await self._pursue(peer, ctx):
                    considered.discard(peer.uid)  # try again on its next advert
                if self.done.is_set():
                    return
            await ctx.wait_for_peers()

    async def _pursue(self, peer: Peer, ctx: Context) -> bool:
        """True if this peer is settled (helped, or can't); False to retry later."""
        decision = await self._decide(peer, ctx)
        if decision is None:
            return False
        ctx.log("reasoning about %s: useful=%s — %s", peer.uid, decision["useful"], decision["reason"])
        if not decision["useful"]:
            return True

        capability = decision["capability"] if peer.has(decision["capability"]) else peer.capabilities[0]
        question = decision["question"] or await self._write_question(peer, ctx)
        try:
            output = await ctx.ask(peer, capability, {"question": question})
        except TimeoutError:
            ctx.log("%s did not answer; will retry on its next advert", peer.uid)
            return False
        except RequestError as exc:
            ctx.log("%s could not help: %s", peer.uid, exc)
            return True
        answer = str(output.get("answer", output))

        outcome = await ctx.reasoner.complete(
            system=self.system_prompt,
            prompt=(
                f"Your goal: {self.goal}\n"
                f"You asked {peer.name} ({peer.description}): {question}\n"
                f"It answered: {answer}\n\n"
                "Now complete your goal based on this answer."
            ),
            temperature=self.temperature,
        )
        self.result = {
            "goal": self.goal,
            "asked": {"uid": peer.uid, "name": peer.name, "capability": capability, "question": question},
            "answer": answer,
            "outcome": outcome.strip(),
            "reasoning": decision["reason"],
        }
        self.done.set()
        ctx.log(
            "\n=== Goal complete ===\ngoal      : %s\nasked     : %s (%s)\nwhy       : %s\n"
            "question  : %s\nanswer    : %s\noutcome   : %s\n",
            self.goal, peer.name, peer.uid, decision["reason"], question, answer, self.result["outcome"],
        )
        return True

    async def _decide(self, peer: Peer, ctx: Context) -> dict[str, Any] | None:
        raw = await ctx.reasoner.complete(
            system=f"{self.system_prompt}\n\nYour goal: {self.goal}",
            prompt=(
                "Another agent on the local network advertised itself:\n"
                f"- name: {peer.name}\n"
                f"- description: {peer.description}\n"
                f"- capabilities: {', '.join(peer.capabilities)}\n\n"
                "Can this agent give you information you need for your goal? "
                "Reply in JSON with keys:\n"
                '- "useful": true or false\n'
                '- "reason": one sentence explaining your decision\n'
                '- "capability": if useful, which of its capabilities you will use; otherwise ""\n'
                '- "question": if useful, the short question you will send to this '
                "agent to get the missing information (addressed to it, about what "
                'it provides — not your own goal); otherwise ""'
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
        """Small models often decide well but leave the JSON question empty."""
        question = await ctx.reasoner.complete(
            system=f"{self.system_prompt}\n\nYour goal: {self.goal}",
            prompt=(
                f"You will ask {peer.name} ({peer.description}) for the information "
                "you are missing. Write only that one short question, nothing else."
            ),
            temperature=0,
        )
        return question.strip().strip('"') or f"Can you help with: {self.goal}"
