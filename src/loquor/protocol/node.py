"""The protocol node: identity + envelopes + discovery + handshake + crypto.

One :class:`Node` per agent. It owns the transport and does everything the
core protocol requires; the agent layer only sees decrypted message bodies
and a peer directory.

Flow between two nodes A and B on one transport::

    A ──beacon──▶ *            every beacon_interval (visible nodes only)
    B hears an unknown prefix ──HELLO(pk, card)──▶ *        (broadcast)
    A ──HELLO_ACK(pk, card, chosen extensions)──▶ B         (unicast)
    both derive the pair key; from here every body is encrypted
    A ──MSG──▶ B ──ACK(msg_id)──▶ A

Rules: drop own envelopes, repeats (``msg_id`` LRU), envelopes addressed to
someone else, bad signatures, MSGs from peers without a handshake. Unknown
envelope types are ignored. Extensions register handlers for their types.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import cbor2

from loquor.protocol.card import AgentCard
from loquor.protocol.crypto import DecryptError, decrypt, encrypt, pair_key
from loquor.protocol.discovery import (
    Beacon,
    Peer,
    PeerDirectory,
    Visibility,
    bloom,
    is_beacon,
    prefix_of,
    slug,
)
from loquor.protocol.envelope import (
    BROADCAST,
    DEFAULT_TTL,
    DecodeError,
    Dedup,
    Envelope,
    Type,
    type_name,
)
from loquor.protocol.identity import Identity, agent_id_of
from loquor.protocol.transport import Address, Transport

log = logging.getLogger("loquor")
# One line per envelope sent or received. Separate logger so it can be
# filtered (e.g. hide handshakes) without hiding the agent's own log.
wire = logging.getLogger("loquor.wire")

MessageHandler = Callable[[Peer, dict[str, Any]], Awaitable[None]]
TypeHandler = Callable[[Envelope, Peer, Address], Awaitable[None]]
PeerHandler = Callable[[Peer], Awaitable[None]]
OverheardHandler = Callable[[Envelope], None]


class Node:
    def __init__(
        self,
        identity: Identity,
        transport: Transport,
        card: AgentCard,
        *,
        visibility: Visibility = Visibility.VISIBLE,
        beacon_interval: float = 10.0,
        ttl: int = DEFAULT_TTL,
    ):
        self.identity = identity
        self.transport = transport
        self.card = card
        self.visibility = visibility
        self.beacon_interval = beacon_interval
        self.ttl = ttl
        self.seq = 0  # bumps with update_card()

        self.peers = PeerDirectory(ttl=3 * beacon_interval)
        self.dedup = Dedup()
        self.keys: dict[bytes, bytes] = {}  # agent_id -> pair key
        self.peer_heard = asyncio.Event()  # set on every beacon/HELLO; consumers clear it

        self._message_handlers: list[MessageHandler] = []
        self._type_handlers: dict[int, TypeHandler] = {}
        self._peer_handlers: list[PeerHandler] = []
        self._overheard_handlers: list[OverheardHandler] = []
        self._acks: dict[bytes, asyncio.Future[None]] = {}
        self._hello_at: dict[bytes, float] = {}  # prefix -> when we last HELLO'd it
        self._initiated: set[bytes] = set()  # agent ids we HELLO'd first (for silent nodes)
        self._tasks: set[asyncio.Task] = set()

    # -------------------------------------------------------------- facade

    @property
    def agent_id(self) -> bytes:
        return self.identity.agent_id

    @property
    def label(self) -> str:
        return f"{slug(self.card.name) or 'agent'}-{self.agent_id[:4].hex()}"

    def on_message(self, handler: MessageHandler) -> None:
        """Called with (peer, body) for every decrypted MSG."""
        self._message_handlers.append(handler)

    def register(self, type_: int, handler: TypeHandler) -> None:
        """Extensions handle their own envelope types (from handshaken peers)."""
        if type_ in (Type.HELLO, Type.HELLO_ACK, Type.MSG, Type.ACK):
            raise ValueError("core types cannot be overridden")
        self._type_handlers[type_] = handler
        if type_ not in self.card.extensions:
            self.card.extensions.append(type_)

    def on_peer(self, handler: PeerHandler) -> None:
        """Called with the peer after every HELLO / HELLO_ACK (trust, relay)."""
        self._peer_handlers.append(handler)

    def on_overheard(self, handler: OverheardHandler) -> None:
        """Called with valid-looking envelopes addressed to someone else (relays)."""
        self._overheard_handlers.append(handler)

    def update_card(self, card: AgentCard) -> None:
        """Change capabilities; the next beacon carries a new seq."""
        self.card = card
        self.seq = (self.seq + 1) & 0xFF

    # ----------------------------------------------------------- lifecycle

    async def start(self) -> None:
        await self.transport.start(self._on_receive)
        self._spawn(self._beacon_loop())
        log.info("%s started (id %s, %s): %s", self.label, self.agent_id.hex(),
                 self.visibility.value, ", ".join(self.card.capabilities) or "no capabilities")

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.transport.stop()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("%s: task failed", self.label, exc_info=task.exception())

    # ------------------------------------------------------------- sending

    async def send(self, peer: Peer, body: dict[str, Any], *, wait_ack: bool = False, timeout: float = 5.0) -> None:
        """Encrypt and send one MSG to a handshaken peer; optionally await its ACK."""
        key = self.keys.get(peer.agent_id)
        if key is None:
            raise ValueError(f"no handshake with {peer.label}")
        env = Envelope(type=Type.MSG, from_id=self.agent_id, to=peer.agent_id, ttl=self.ttl)
        env.body = encrypt(key, env, cbor2.dumps(body))
        future = None
        if wait_ack:
            future = asyncio.get_running_loop().create_future()
            self._acks[env.msg_id] = future
        try:
            await self._send(env, peer.address)
            if future is not None:
                await asyncio.wait_for(future, timeout)
        finally:
            self._acks.pop(env.msg_id, None)

    async def send_type(self, type_: int, body: bytes, peer: Peer | None = None) -> Envelope:
        """Extensions: send a plaintext-bodied envelope, unicast or broadcast."""
        env = Envelope(type=type_, from_id=self.agent_id, to=peer.agent_id if peer else BROADCAST,
                       body=body, ttl=self.ttl)
        await self._send(env, peer.address if peer else None)
        return env

    async def hello(self, peer: Peer | None = None, *, ack: bool = False) -> None:
        """HELLO (broadcast or to a peer) or HELLO_ACK (to a peer)."""
        body: dict[str, Any] = {"pk": self.identity.public_key, "card": self.card.to_dict(),
                                "seq": self.seq, "ext": sorted(set(self.card.extensions))}
        if ack:
            assert peer is not None
            body["chosen"] = sorted(set(self.card.extensions) & set(peer.extensions))
        env = Envelope(type=Type.HELLO_ACK if ack else Type.HELLO, from_id=self.agent_id,
                       to=peer.agent_id if peer else BROADCAST, body=cbor2.dumps(body), ttl=self.ttl)
        if peer is not None and not ack:
            self._initiated.add(peer.agent_id)
        await self._send(env, peer.address if peer else None)

    async def _send(self, env: Envelope, address: Address | None) -> None:
        data = env.sign(self.identity).encode()
        if len(data) > self.transport.max_payload:
            raise ValueError(f"envelope too large for transport ({len(data)} > {self.transport.max_payload})")
        if address is None or env.is_broadcast:
            await self.transport.broadcast(data)
        else:
            await self.transport.send(address, data)
        _log_wire("SEND", env, len(data), self)

    # ------------------------------------------------------------ receiving

    def _on_receive(self, data: bytes, address: Address) -> None:
        if is_beacon(data):
            try:
                self._on_beacon(Beacon.decode(data), address)
            except ValueError as exc:
                wire.debug("DROP  beacon: %s", exc)
            return
        try:
            env = Envelope.decode(data)
        except DecodeError as exc:
            wire.info("DROP  %d bytes: %s", len(data), exc)
            return
        if env.from_id == self.agent_id:
            return  # our own, looped back by the transport
        if self.dedup.seen(env.msg_id):
            wire.debug("DROP  duplicate %s", env.msg_id[:4].hex())
            return
        if not env.is_broadcast and env.to != self.agent_id:
            _log_wire("RECV", env, len(data), self, "not for me")
            for handler in self._overheard_handlers:
                handler(env)
            return
        _log_wire("RECV", env, len(data), self)
        self._spawn(self._dispatch(env, address))

    async def _dispatch(self, env: Envelope, address: Address) -> None:
        if env.type in (Type.HELLO, Type.HELLO_ACK):
            await self._on_hello(env, address, ack=env.type == Type.HELLO_ACK)
            return
        peer = self.peers.get(env.from_id)
        if peer is None or not peer.handshaken:
            wire.info("DROP  %s from %s: no handshake", type_name(env.type), env.from_id[:4].hex())
            return
        if not env.verify(peer.public_key):
            wire.info("DROP  %s from %s: bad signature", type_name(env.type), peer.label)
            return
        peer.touch(address)
        if env.type == Type.MSG:
            await self._on_msg(env, peer, address)
        elif env.type == Type.ACK:
            future = self._acks.get(env.body)
            if future is not None and not future.done():
                future.set_result(None)
        elif env.type in self._type_handlers:
            await self._type_handlers[env.type](env, peer, address)
        else:
            wire.debug("IGNORE %s from %s: unknown type", type_name(env.type), peer.label)

    def _on_beacon(self, beacon: Beacon, address: Address) -> None:
        if beacon.prefix == prefix_of(self.agent_id):
            return
        peer, changed = self.peers.heard_beacon(beacon, address)
        self.peer_heard.set()
        now = time.monotonic()
        if peer is not None and not changed:
            return
        if now - self._hello_at.get(beacon.prefix, -1e9) < self.beacon_interval:
            return  # already asked recently
        self._hello_at[beacon.prefix] = now
        # Unknown prefix: we only know 8 bytes of the id, so HELLO everyone.
        # Known peer with new seq: its capabilities changed, refresh its card.
        self._spawn(self.hello(peer))

    async def _on_hello(self, env: Envelope, address: Address, *, ack: bool) -> None:
        try:
            body = cbor2.loads(env.body)
            public_key = body["pk"]
            card = AgentCard.from_dict(body["card"])
            if agent_id_of(public_key) != env.from_id:
                raise ValueError("public key does not match agent id")
        except (Exception,) as exc:
            wire.info("DROP  %s from %s: %s", type_name(env.type), env.from_id[:4].hex(), exc)
            return
        if not env.verify(public_key):
            wire.info("DROP  %s from %s: bad signature", type_name(env.type), env.from_id[:4].hex())
            return
        if not ack and not self.visibility.answers_hello and env.from_id not in self._initiated:
            wire.debug("IGNORE HELLO from %s: silent", env.from_id[:4].hex())
            return
        peer, new = self.peers.heard_hello(env.from_id, public_key, card.to_dict(), address)
        peer.extensions = [int(e) for e in body.get("ext", [])]  # what it supports; chosen = intersection
        peer.seq = int(body.get("seq", 0)) & 0xFF  # the card version its beacons will carry
        self.keys[peer.agent_id] = pair_key(self.identity, public_key, peer.agent_id)
        if new:
            log.info("%s met %s: %s %s", self.label, peer.label, peer.description, peer.capabilities)
        self.peer_heard.set()
        for handler in self._peer_handlers:
            await handler(peer)
        if not ack:
            await self.hello(peer, ack=True)

    async def _on_msg(self, env: Envelope, peer: Peer, address: Address) -> None:
        try:
            plaintext = decrypt(self.keys[peer.agent_id], env)
            body = cbor2.loads(plaintext)
        except (DecryptError, KeyError, Exception) as exc:
            wire.info("DROP  MSG from %s: %s", peer.label, exc)
            return
        if not env.is_broadcast:
            await self._send(Envelope(type=Type.ACK, from_id=self.agent_id, to=peer.agent_id,
                                      body=env.msg_id, ttl=self.ttl), address)
        for handler in self._message_handlers:
            await handler(peer, body)

    # ------------------------------------------------------------- beacons

    async def _beacon_loop(self) -> None:
        while True:
            if self.visibility.beacons:
                beacon = Beacon(prefix_of(self.agent_id), bloom(self.card.capabilities), self.seq)
                await self.transport.broadcast(beacon.encode())
                wire.debug("SEND  beacon seq=%d bloom=%s", self.seq, beacon.bloom.hex())
            for peer in self.peers.prune():
                self.keys.pop(peer.agent_id, None)
                log.info("%s lost %s (silent for %.0fs)", self.label, peer.label, self.peers.ttl)
            await asyncio.sleep(self.beacon_interval)


def _log_wire(direction: str, env: Envelope, size: int, node: Node, note: str | None = None) -> None:
    """``SEND  MSG        weather-1a2b -> clothing-3c4d  [msg 0a1b2c3d, 412 B]``"""
    def who(agent_id: bytes) -> str:
        if agent_id == node.agent_id:
            return node.label
        if agent_id == BROADCAST:
            return "*"
        peer = node.peers.get(agent_id)
        return peer.label if peer else agent_id[:4].hex()

    wire.info("%-4s  %-10s %s -> %s  [msg %s, %d B]%s", direction, type_name(env.type),
              who(env.from_id), who(env.to), env.msg_id[:4].hex(), size, f" ({note})" if note else "")
