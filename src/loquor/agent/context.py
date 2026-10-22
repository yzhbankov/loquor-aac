"""What a skill can use: the reasoner, the peers, ``ask``, sensors and triggers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from loquor.agent.sensors import SensorHub
from loquor.agent.triggers import TriggerHub
from loquor.protocol.discovery import Peer
from loquor.reasoning.base import Reasoner

if TYPE_CHECKING:
    from loquor.agent.agent import Agent

log = logging.getLogger("loquor")


class Context:
    def __init__(self, agent: Agent):
        self._agent = agent

    @property
    def uid(self) -> str:
        return self._agent.uid

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def reasoner(self) -> Reasoner:
        return self._agent.reasoner

    @property
    def sensors(self) -> SensorHub:
        """This agent's sensors: ``readings()``, ``latest()``, ``read()``, ``describe()``."""
        return self._agent.sensors

    @property
    def triggers(self) -> TriggerHub:
        """This agent's triggers: ``propose()`` (ask the LLM) and ``fire()``."""
        return self._agent.triggers

    def remote_sensors(self) -> dict[str, Peer]:
        """Peers' sensors mirrored into ``sensors``: hub name (``node:sensor``) -> peer."""
        return dict(self._agent.remote_sensors)

    def remote_triggers(self) -> dict[str, Peer]:
        """Peers' triggers mirrored into ``triggers``: hub name (``node:trigger``) -> peer."""
        return dict(self._agent.remote_triggers)

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
