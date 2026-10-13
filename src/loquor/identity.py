"""Peer identity and message authentication.

PoC model: paired identities with a pre-shared pairing secret. Every message
is signed with HMAC-SHA256 over its canonical JSON; receivers reject messages
whose signature does not verify. This gives the demo mutual authentication
and integrity on untrusted local networks without a PKI.

A production implementation would replace the shared secret with per-device
keypairs and pinned public credentials exchanged during an explicit pairing
ceremony (and transport-level TLS).
"""

from __future__ import annotations

import hashlib
import hmac

from loquor.messages import Envelope


class PairingIdentity:
    """Signs and verifies envelopes using a pairing secret shared by peers."""

    def __init__(self, agent_id: str, pairing_secret: str):
        if not pairing_secret:
            raise ValueError("pairing_secret must be non-empty")
        self.agent_id = agent_id
        self._key = pairing_secret.encode()

    def sign(self, envelope: Envelope) -> Envelope:
        digest = hmac.new(self._key, envelope.signing_bytes(), hashlib.sha256).hexdigest()
        return envelope.model_copy(update={"signature": digest})

    def verify(self, envelope: Envelope) -> bool:
        if not envelope.signature:
            return False
        expected = hmac.new(self._key, envelope.signing_bytes(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, envelope.signature)
