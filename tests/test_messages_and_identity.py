"""Message validation and peer authentication."""

import pytest
from pydantic import ValidationError

from loquor.identity import PairingIdentity
from loquor.messages import Envelope, MessageType, task_request


def test_envelope_roundtrip():
    envelope = task_request("pump-01", "maintenance_diagnostics", {"vibration_mm_s": 8.2})
    wire = envelope.model_dump(mode="json")
    parsed = Envelope.model_validate(wire)
    assert parsed == envelope
    assert parsed.type is MessageType.TASK_REQUEST
    assert parsed.payload["observations"]["vibration_mm_s"] == 8.2


def test_invalid_envelope_rejected():
    with pytest.raises(ValidationError):
        Envelope.model_validate({"type": "not_a_real_type", "task_id": "t", "sender": "x"})


def test_sign_and_verify():
    identity = PairingIdentity("pump-01", "secret")
    signed = identity.sign(task_request("pump-01", "diag", {"v": 1}))
    assert identity.verify(signed)


def test_tampered_message_fails_verification():
    identity = PairingIdentity("pump-01", "secret")
    signed = identity.sign(task_request("pump-01", "diag", {"v": 1}))
    tampered = signed.model_copy(update={"payload": {"observations": {"v": 999}}})
    assert not identity.verify(tampered)


def test_wrong_secret_fails_verification():
    signed = PairingIdentity("pump-01", "secret-a").sign(task_request("pump-01", "diag", {}))
    assert not PairingIdentity("inspector-01", "secret-b").verify(signed)


def test_unsigned_message_fails_verification():
    identity = PairingIdentity("pump-01", "secret")
    assert not identity.verify(task_request("pump-01", "diag", {}))
