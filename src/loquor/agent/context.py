"""What a skill can use: the reasoner, the peers, and ``ask``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from loquor.protocol.discovery import Peer
from loquor.reasoning.base import Reasoner

if TYPE_CHECKING:
    from loquor.agent.agent import Agent

log = logging.getLogger("loquor")


class Context:
    def __init__(self, agent: Agent):
        self._agent = agent
        #: Reserved for the sensors layer; empty until it exists.
        self.sensors: dict[str, Any] = {}

    @property
    def uid(self) -> str:
        return self._agent.uid

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def reasoner(self) -> Reasoner:
        return self._agent.reasoner

    def peers(self, capability: str | None = None) -> list[Peer]:
        """Handshaken peers, optionally only those with a capability."""
        return self._agent.node.peers.find(capability)

    async def ask(
        self, peer: Peer, capability: str, input: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """Send a request to one peer and return its ``output``.

        Raises :class:`~loquor.agent.tracker.RequestError` if the peer answers
        with an error, ``TimeoutError`` if it never answers.
        """
        return await self._agent.ask(peer, capability, input, timeout=timeout)

    async def query(self, capability: str, timeout: float = 1.0) -> list[Peer]:
        """Ask the mesh who offers a capability (QUERY/OFFER); returns the peers."""
        return await self._agent.query.query(capability, timeout)

    async def wait_for_peers(self) -> None:
        """Return when the next beacon or HELLO is heard."""
        event = self._agent.node.peer_heard
        await event.wait()
        event.clear()

    def log(self, msg: str, *args: Any) -> None:
        log.info("%s: " + msg, self.uid, *args)
