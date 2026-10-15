"""Request bookkeeping on both sides of an exchange.

Outbound: a ``request_id`` maps to the future its response will resolve.
Inbound: ``(sender, request_id)`` maps to the reply already sent, so a
retried request gets the same answer again — the skill never runs twice for
one request, and never produces two different answers.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from loquor.protocol.message import Message

Key = tuple[str, str]  # (sender uid, request_id)


class RequestError(Exception):
    """The peer answered with an ``error`` message."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class RequestTracker:
    def __init__(self, cache_size: int = 1024):
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._answers: OrderedDict[Key, Message] = OrderedDict()
        self._answering: set[Key] = set()
        self._cache_size = cache_size

    # ------------------------------------------------- outbound (requester)

    def open(self, request_id: str) -> asyncio.Future[dict[str, Any]]:
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, request_id: str, output: dict[str, Any]) -> None:
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(output)

    def fail(self, request_id: str, exc: Exception) -> None:
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_exception(exc)

    def close(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    # ------------------------------------------------- inbound (responder)

    def cached(self, key: Key) -> Message | None:
        return self._answers.get(key)

    def begin(self, key: Key) -> bool:
        """Claim a request. False if its first copy is still being answered."""
        if key in self._answering:
            return False
        self._answering.add(key)
        return True

    def finish(self, key: Key, reply: Message | None) -> None:
        self._answering.discard(key)
        if reply is not None:
            self._answers[key] = reply
            while len(self._answers) > self._cache_size:
                self._answers.popitem(last=False)
