"""The envelope: the only mandatory wire unit (AMP v1).

CBOR array, fixed order::

    [ver, type, msg_id, from, to, ttl, ts, body, sig]

- ``ver``     1
- ``type``    uint; 1-4 core, 5-8 extensions, 16+ reserved; unknown types
              decode fine and are ignored by the receiver
- ``msg_id``  16 random bytes; receivers drop repeats (LRU)
- ``from``    sender agent_id (16 bytes)
- ``to``      recipient agent_id, or 16 zero bytes for broadcast
- ``ttl``     hop budget; a relay decrements it and drops at 0
- ``ts``      unix seconds
- ``body``    bytes; plaintext for HELLO/HELLO_ACK, encrypted for the rest
- ``sig``     Ed25519 over the CBOR of ``[ver, type, msg_id, from, to, ts, body]``

``ttl`` is *not* signed: a relay must be able to decrement it without the
origin's key. Everything else is covered, so a relay can read the header but
change nothing. Envelopes are connectionless; no link-level session state is
needed to receive one. Maximum encoded size is 64 KB.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum

import cbor2

from loquor.protocol.identity import AGENT_ID_SIZE, Identity, verify

VERSION = 1
MAX_ENVELOPE_SIZE = 65536
BROADCAST = bytes(AGENT_ID_SIZE)
DEFAULT_TTL = 4


class Type(IntEnum):
    HELLO = 1
    HELLO_ACK = 2
    MSG = 3
    ACK = 4
    QUERY = 5
    OFFER = 6
    SESSION_REQ = 7
    SESSION_ACK = 8


CORE_TYPES = {Type.HELLO, Type.HELLO_ACK, Type.MSG, Type.ACK}
RESERVED_FROM = 16


def type_name(type_: int) -> str:
    try:
        return Type(type_).name
    except ValueError:
        return f"type{type_}"


class DecodeError(ValueError):
    """The bytes are not an envelope this node understands."""


@dataclass
class Envelope:
    type: int
    from_id: bytes
    to: bytes = BROADCAST
    body: bytes = b""
    ttl: int = DEFAULT_TTL
    ts: int = field(default_factory=lambda: int(time.time()))
    msg_id: bytes = field(default_factory=lambda: os.urandom(16))
    ver: int = VERSION
    sig: bytes | None = None

    @property
    def is_broadcast(self) -> bool:
        return self.to == BROADCAST

    def signed_bytes(self) -> bytes:
        return cbor2.dumps([self.ver, self.type, self.msg_id, self.from_id, self.to, self.ts, self.body])

    def encode(self) -> bytes:
        data = cbor2.dumps(
            [self.ver, self.type, self.msg_id, self.from_id, self.to, self.ttl, self.ts, self.body, self.sig or b""]
        )
        if len(data) > MAX_ENVELOPE_SIZE:
            raise ValueError(f"envelope too large ({len(data)} > {MAX_ENVELOPE_SIZE} bytes)")
        return data

    @classmethod
    def decode(cls, data: bytes) -> Envelope:
        if len(data) > MAX_ENVELOPE_SIZE:
            raise DecodeError(f"envelope too large ({len(data)} bytes)")
        try:
            fields = cbor2.loads(data)
        except Exception as exc:  # cbor2 raises several error classes
            raise DecodeError("not CBOR") from exc
        if not isinstance(fields, list) or len(fields) != 9:
            raise DecodeError("not an envelope")
        ver, type_, msg_id, from_id, to, ttl, ts, body, sig = fields
        if ver != VERSION:
            raise DecodeError(f"unsupported version {ver!r}")
        if not (isinstance(type_, int) and type_ >= 0):
            raise DecodeError("bad type")
        for name, value, size in (("msg_id", msg_id, 16), ("from", from_id, AGENT_ID_SIZE), ("to", to, AGENT_ID_SIZE)):
            if not (isinstance(value, bytes) and len(value) == size):
                raise DecodeError(f"bad {name}")
        if not (isinstance(ttl, int) and ttl >= 0 and isinstance(ts, int)):
            raise DecodeError("bad ttl or ts")
        if not isinstance(body, bytes) or not isinstance(sig, bytes):
            raise DecodeError("bad body or sig")
        return cls(type=type_, from_id=from_id, to=to, body=body, ttl=ttl, ts=ts,
                   msg_id=msg_id, ver=ver, sig=sig or None)

    def sign(self, identity: Identity) -> Envelope:
        if identity.agent_id != self.from_id:
            raise ValueError("signing identity does not match from")
        self.sig = identity.sign(self.signed_bytes())
        return self

    def verify(self, public_key: bytes) -> bool:
        return self.sig is not None and verify(public_key, self.signed_bytes(), self.sig)

    def relayed(self) -> Envelope | None:
        """A copy with ``ttl`` decremented, or None if it must be dropped."""
        if self.ttl <= 1:
            return None
        return Envelope(type=self.type, from_id=self.from_id, to=self.to, body=self.body,
                        ttl=self.ttl - 1, ts=self.ts, msg_id=self.msg_id, ver=self.ver, sig=self.sig)


class Dedup:
    """Remembers recent ``msg_id``s; ``seen()`` is True for a repeat."""

    def __init__(self, capacity: int = 4096):
        self.capacity = capacity
        self._ids: OrderedDict[bytes, None] = OrderedDict()

    def seen(self, msg_id: bytes) -> bool:
        if msg_id in self._ids:
            self._ids.move_to_end(msg_id)
            return True
        self._ids[msg_id] = None
        if len(self._ids) > self.capacity:
            self._ids.popitem(last=False)
        return False

    def __len__(self) -> int:
        return len(self._ids)
