"""The nurse's skill: ask every chair, let the LLM order the patients, show the board.

Runs once the nurse's device hears the chairs: waits until ``expected_chairs``
are heard, or ``settle`` seconds after the first one. Asks each chair for
``patient_status`` (the chairs run their own assessment), hands all statuses
to the LLM for an ordering with reasons and recommendations, then fires
the ``triage_board`` trigger with the result. The LLM decided the order; the
trigger input is built by code, so ``fire()`` is called directly.

    {"type": "triage_round.py:TriageRound", "expected_chairs": 3, "settle": 10}
"""

from __future__ import annotations

import asyncio
import json
import time

from loquor import RequestError, Skill, TriggerError

RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chair": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["chair", "reason"],
            },
        },
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["order", "recommendations"],
}

DEFAULT_PROMPT = (
    "You assist a triage nurse arriving at the emergency triage area. Automated chairs have "
    "assessed the seated patients. Decide the order in which the nurse should see them — "
    "the most time-critical first — and give short, practical recommendations. Be concise."
)


class TriageRound(Skill):
    def __init__(self, system_prompt: str = DEFAULT_PROMPT, expected_chairs: int = 0,
                 settle: float = 10.0, board: str = "triage_board", ask_timeout: float = 90.0):
        self.system_prompt = system_prompt
        self.expected_chairs = expected_chairs
        self.settle = settle
        self.board = board
        self.ask_timeout = ask_timeout
        self.triggers = (board,)
        self.result: dict | None = None
        self.done = asyncio.Event()

    async def run(self, ctx):
        chairs = await self._wait_for_chairs(ctx)
        ctx.log("asking %d chair(s) for their patients' status", len(chairs))
        statuses = []
        for chair in chairs:
            try:
                statuses.append(await ctx.ask(chair, "patient_status", {}, timeout=self.ask_timeout))
            except (RequestError, TimeoutError) as exc:
                ctx.log("%s gave no status: %s", chair.label, exc)
        if not statuses:
            ctx.log("no patient statuses; nothing to do")
            return

        board = await self._rank(statuses, ctx)
        self.result = board
        self.done.set()
        try:
            await ctx.triggers.fire(self.board, board)
        except TriggerError as exc:
            ctx.log("board failed: %s", exc)

    async def _wait_for_chairs(self, ctx):
        first_seen = None
        while True:
            chairs = ctx.peers("patient_status")
            if chairs and self.expected_chairs and len(chairs) >= self.expected_chairs:
                return chairs
            if chairs:
                first_seen = first_seen or time.monotonic()
                if time.monotonic() - first_seen >= self.settle:
                    return chairs
            try:
                await asyncio.wait_for(ctx.wait_for_peers(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def _rank(self, statuses, ctx) -> dict:
        by_chair = {s["chair"]: s for s in statuses}
        lines = [
            f"- {s['chair']}: severity {s['severity']}, {s['urgency']}; concern: {s['concern']}. "
            f"Vitals: {json.dumps(s.get('vitals', {}))}. Patient says: \"{s.get('complaint', '')}\""
            for s in statuses
        ]
        raw = await ctx.reasoner.complete(
            system=self.system_prompt,
            prompt=(
                "Patients waiting, as assessed by their chairs:\n" + "\n".join(lines) + "\n\n"
                "Order them by who must be seen first. Reply in JSON with keys: "
                '"order" (list of {"chair": the chair name exactly as given, "reason": one line}), '
                '"recommendations" (2-4 short practical lines for the nurse).'
            ),
            temperature=0,
            schema=RANKING_SCHEMA,
        )
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        order, seen = [], set()
        for item in data.get("order", []) if isinstance(data, dict) else []:
            chair = str(item.get("chair", "")) if isinstance(item, dict) else ""
            if chair in by_chair and chair not in seen:
                seen.add(chair)
                order.append((chair, str(item.get("reason", ""))))
        # Anything the model skipped goes at the end, most severe first: nobody is forgotten.
        for s in sorted(statuses, key=lambda s: s["severity"]):
            if s["chair"] not in seen:
                order.append((s["chair"], "not ranked by the model; placed by severity"))
        patients = [
            {"chair": chair, "priority": i + 1, "severity": by_chair[chair]["severity"],
             "urgency": by_chair[chair]["urgency"], "concern": by_chair[chair]["concern"], "reason": reason}
            for i, (chair, reason) in enumerate(order)
        ]
        recommendations = [str(r) for r in data.get("recommendations", [])] if isinstance(data, dict) else []
        return {"patients": patients, "recommendations": recommendations[:4]}
