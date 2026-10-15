"""Agent identity: an Ed25519 keypair, generated once and persisted.

``agent_id`` is the first 16 bytes of SHA-256(public key). All addressing
uses agent ids — never a device, MAC or IP identifier — so an agent keeps
its identity across transports and restarts.

The same keypair also gives the X25519 key used for pairwise encryption
(see :mod:`loquor.protocol.crypto`).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

AGENT_ID_SIZE = 16
PUBLIC_KEY_SIZE = 32
SIGNATURE_SIZE = 64


def agent_id_of(public_key: bytes) -> bytes:
    if len(public_key) != PUBLIC_KEY_SIZE:
        raise ValueError(f"public key must be {PUBLIC_KEY_SIZE} bytes")
    return hashlib.sha256(public_key).digest()[:AGENT_ID_SIZE]


def verify(public_key: bytes, data: bytes, signature: bytes) -> bool:
    """True if ``signature`` is a valid Ed25519 signature of ``data``."""
    if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    try:
        VerifyKey(public_key).verify(data, signature)
        return True
    except BadSignatureError:
        return False


class Identity:
    def __init__(self, signing_key: SigningKey):
        self._key = signing_key
        self.public_key: bytes = bytes(signing_key.verify_key)
        self.agent_id: bytes = agent_id_of(self.public_key)

    @classmethod
    def generate(cls) -> Identity:
        return cls(SigningKey.generate())

    @classmethod
    def from_seed(cls, seed: bytes) -> Identity:
        return cls(SigningKey(seed))

    @classmethod
    def load_or_create(cls, path: str | Path) -> Identity:
        """Read the 32-byte seed at ``path``; create it (mode 0600) if absent."""
        path = Path(path)
        if path.exists():
            seed = path.read_bytes()
            if len(seed) != 32:
                raise ValueError(f"{path}: not a 32-byte key seed")
            return cls.from_seed(seed)
        identity = cls.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(bytes(identity._key))
        return identity

    @property
    def seed(self) -> bytes:
        return bytes(self._key)

    def sign(self, data: bytes) -> bytes:
        return self._key.sign(data).signature

    def x25519_private_key(self) -> bytes:
        """This identity's key converted for X25519 key agreement."""
        return bytes(self._key.to_curve25519_private_key())

    @property
    def short_id(self) -> str:
        return self.agent_id[:4].hex()

    def __repr__(self) -> str:
        return f"Identity({self.agent_id.hex()})"


def x25519_public_key(ed25519_public_key: bytes) -> bytes:
    """A peer's Ed25519 public key converted for X25519 key agreement."""
    return bytes(VerifyKey(ed25519_public_key).to_curve25519_public_key())
