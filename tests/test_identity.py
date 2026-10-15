"""§1 Identity: Ed25519 keypair, agent_id = SHA-256(pk)[:16], persisted."""

import hashlib
import os
import stat

import pytest

from loquor.protocol.identity import Identity, agent_id_of, verify, x25519_public_key


def test_agent_id_is_sha256_prefix_of_public_key():
    identity = Identity.generate()
    assert len(identity.public_key) == 32
    assert identity.agent_id == hashlib.sha256(identity.public_key).digest()[:16]
    assert agent_id_of(identity.public_key) == identity.agent_id
    with pytest.raises(ValueError):
        agent_id_of(b"short")


def test_sign_and_verify():
    identity = Identity.generate()
    signature = identity.sign(b"hello")
    assert len(signature) == 64
    assert verify(identity.public_key, b"hello", signature)
    assert not verify(identity.public_key, b"hellp", signature)
    assert not verify(Identity.generate().public_key, b"hello", signature)
    assert not verify(identity.public_key, b"hello", b"x" * 64)
    assert not verify(identity.public_key, b"hello", b"short")


def test_persisted_identity_survives_restart(tmp_path):
    path = tmp_path / "keys" / "agent.key"
    first = Identity.load_or_create(path)
    second = Identity.load_or_create(path)
    assert first.agent_id == second.agent_id and first.seed == second.seed
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Identity.load_or_create(tmp_path / "other.key").agent_id != first.agent_id
    (tmp_path / "bad.key").write_bytes(b"nope")
    with pytest.raises(ValueError, match="32-byte"):
        Identity.load_or_create(tmp_path / "bad.key")


def test_x25519_conversion_is_consistent():
    a, b = Identity.generate(), Identity.generate()
    from nacl.bindings import crypto_scalarmult

    shared_ab = crypto_scalarmult(a.x25519_private_key(), x25519_public_key(b.public_key))
    shared_ba = crypto_scalarmult(b.x25519_private_key(), x25519_public_key(a.public_key))
    assert shared_ab == shared_ba and len(shared_ab) == 32
