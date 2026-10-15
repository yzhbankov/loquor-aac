"""§2 Envelope: CBOR round-trip, sign/verify, dedup, ttl, size, unknown types."""

import cbor2
import pytest

from loquor.protocol.envelope import (
    BROADCAST,
    MAX_ENVELOPE_SIZE,
    DecodeError,
    Dedup,
    Envelope,
    Type,
    type_name,
)
from loquor.protocol.identity import Identity


def test_encode_decode_sign_verify_roundtrip():
    me, you = Identity.generate(), Identity.generate()
    env = Envelope(type=Type.MSG, from_id=me.agent_id, to=you.agent_id, body=b"\x01\x02", ttl=3).sign(me)
    data = env.encode()
    back = Envelope.decode(data)
    assert back == env
    assert back.verify(me.public_key)
    assert not back.verify(you.public_key)
    assert cbor2.loads(data)[0] == 1  # ver is the first array element
    assert len(cbor2.loads(data)) == 9


def test_tampering_breaks_signature_except_ttl():
    me = Identity.generate()
    env = Envelope(type=Type.MSG, from_id=me.agent_id, body=b"x").sign(me)
    for change in ({"body": b"y"}, {"to": b"\x01" * 16}, {"ts": env.ts + 1}, {"type": 4}, {"msg_id": b"\x02" * 16}):
        tampered = Envelope(**{**env.__dict__, **change})
        assert not tampered.verify(me.public_key), change
    assert Envelope(**{**env.__dict__, "ttl": 1}).verify(me.public_key)  # relays may decrement
    with pytest.raises(ValueError, match="does not match"):
        Envelope(type=Type.MSG, from_id=me.agent_id).sign(Identity.generate())
    assert not Envelope(type=Type.MSG, from_id=me.agent_id).verify(me.public_key)  # unsigned


def test_broadcast_and_relay_ttl():
    me = Identity.generate()
    env = Envelope(type=Type.HELLO, from_id=me.agent_id, ttl=2)
    assert env.is_broadcast and env.to == BROADCAST
    hop1 = env.relayed()
    assert hop1.ttl == 1 and hop1.msg_id == env.msg_id
    assert hop1.relayed() is None
    assert Envelope(type=Type.MSG, from_id=me.agent_id, ttl=0).relayed() is None


def test_unknown_types_decode_and_are_nameable():
    me = Identity.generate()
    env = Envelope(type=200, from_id=me.agent_id, body=b"future").sign(me)
    back = Envelope.decode(env.encode())
    assert back.type == 200 and back.verify(me.public_key)
    assert type_name(200) == "type200" and type_name(3) == "MSG"


def test_decode_rejects_bad_input():
    me = Identity.generate()
    good = Envelope(type=Type.MSG, from_id=me.agent_id).sign(me)
    with pytest.raises(DecodeError, match="not CBOR"):
        Envelope.decode(b"\xff\xff")
    with pytest.raises(DecodeError, match="not an envelope"):
        Envelope.decode(cbor2.dumps({"hello": 1}))
    fields = cbor2.loads(good.encode())
    with pytest.raises(DecodeError, match="unsupported version"):
        Envelope.decode(cbor2.dumps([2, *fields[1:]]))
    with pytest.raises(DecodeError, match="bad from"):
        Envelope.decode(cbor2.dumps([*fields[:3], b"short", *fields[4:]]))
    with pytest.raises(DecodeError, match="bad type"):
        Envelope.decode(cbor2.dumps([1, "MSG", *fields[2:]]))


def test_size_cap():
    me = Identity.generate()
    big = Envelope(type=Type.MSG, from_id=me.agent_id, body=b"x" * MAX_ENVELOPE_SIZE)
    with pytest.raises(ValueError, match="too large"):
        big.encode()
    with pytest.raises(DecodeError, match="too large"):
        Envelope.decode(b"\x00" * (MAX_ENVELOPE_SIZE + 1))
    assert len(Envelope(type=Type.MSG, from_id=me.agent_id, body=b"x" * 60000).sign(me).encode()) <= MAX_ENVELOPE_SIZE


def test_dedup_is_an_lru():
    d = Dedup(capacity=2)
    a, b, c = b"a" * 16, b"b" * 16, b"c" * 16
    assert not d.seen(a) and not d.seen(b)
    assert d.seen(a)  # repeat; also refreshes a
    assert not d.seen(c)  # evicts b, the least recently seen
    assert not d.seen(b) and len(d) == 2
