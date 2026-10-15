"""Message authentication.

PoC model: every message is HMAC-SHA256-signed over its canonical JSON with
a pairing secret shared by all agents; receivers drop messages whose
signature does not verify. Replacing the shared secret with per-device keys
is a change inside this module only.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from loquor.protocol.message import Message


def canonical_bytes(message: Message) -> bytes:
    """The bytes that are signed: every field but the signature, keys sorted."""
    data = message.model_dump(mode="json", exclude={"signature"})
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


class Signer:
    def __init__(self, pairing_secret: str):
        if not pairing_secret:
            raise ValueError("pairing secret must be non-empty")
        self._key = pairing_secret.encode()

    def sign(self, message: Message) -> Message:
        digest = hmac.new(self._key, canonical_bytes(message), hashlib.sha256).hexdigest()
        return message.model_copy(update={"signature": digest})

    def verify(self, message: Message) -> bool:
        if not message.signature:
            return False
        expected = hmac.new(self._key, canonical_bytes(message), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, message.signature)
