"""Pairwise encryption (AMP §5).

After HELLO / HELLO_ACK both sides derive the same key::

    shared = X25519(my_key, their_key)          # Ed25519 keys converted
    key    = SHA-256("AMP-v1-pair" | shared | min(id_a, id_b) | max(id_a, id_b))

and every later body between that pair is ChaCha20-Poly1305 (IETF, 12-byte
nonce)::

    body = nonce(12) | ciphertext+tag

The envelope header ``[ver, type, msg_id, from, to]`` is the associated data,
so a body cannot be moved to another envelope. Relays never decrypt: they
only read the header. Nonces are random; at this message volume the
collision probability is negligible, and a key only lives for one run.
"""

from __future__ import annotations

import hashlib
import os

import cbor2
from nacl.bindings import (
    crypto_aead_chacha20poly1305_ietf_decrypt,
    crypto_aead_chacha20poly1305_ietf_encrypt,
    crypto_scalarmult,
)
from nacl.exceptions import CryptoError

from loquor.protocol.envelope import Envelope
from loquor.protocol.identity import Identity, x25519_public_key

NONCE_SIZE = 12
_INFO = b"AMP-v1-pair"


class DecryptError(ValueError):
    """The body does not decrypt under this pair's key."""


def pair_key(me: Identity, their_public_key: bytes, their_agent_id: bytes) -> bytes:
    shared = crypto_scalarmult(me.x25519_private_key(), x25519_public_key(their_public_key))
    low, high = sorted((me.agent_id, their_agent_id))
    return hashlib.sha256(_INFO + shared + low + high).digest()


def associated_data(env: Envelope) -> bytes:
    return cbor2.dumps([env.ver, env.type, env.msg_id, env.from_id, env.to])


def encrypt(key: bytes, env: Envelope, plaintext: bytes) -> bytes:
    """The encrypted ``body`` for ``env`` (whose header fields must be final)."""
    nonce = os.urandom(NONCE_SIZE)
    return nonce + crypto_aead_chacha20poly1305_ietf_encrypt(plaintext, associated_data(env), nonce, key)


def decrypt(key: bytes, env: Envelope) -> bytes:
    if len(env.body) < NONCE_SIZE + 16:
        raise DecryptError("body too short")
    nonce, ciphertext = env.body[:NONCE_SIZE], env.body[NONCE_SIZE:]
    try:
        return crypto_aead_chacha20poly1305_ietf_decrypt(ciphertext, associated_data(env), nonce, key)
    except CryptoError as exc:
        raise DecryptError("authentication failed") from exc
