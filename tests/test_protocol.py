"""Message format, signing, codec."""

import json

import pytest

from loquor.protocol import (
    MAX_MESSAGE_SIZE,
    DecodeError,
    MessageType,
    Signer,
    advert,
    decode,
    encode,
    error,
    request,
    response,
)


def test_constructors_fill_payloads():
    a = advert("w-1", "Weather", "I forecast.", ["weather_forecast"])
    assert a.type is MessageType.ADVERT and a.recipient is None
    assert a.payload == {"name": "Weather", "description": "I forecast.", "capabilities": ["weather_forecast"]}
    r = request("c-1", "Clothing", "w-1", "rid", "weather_forecast", {"question": "?"})
    assert r.recipient == "w-1" and r.request_id == "rid"
    assert response("w-1", "Weather", "c-1", "rid", {"answer": "sunny"}).payload["output"] == {"answer": "sunny"}
    assert error("w-1", "Weather", "c-1", "rid", "skill_failed", "boom").payload["code"] == "skill_failed"


def test_sign_and_verify():
    signer = Signer("secret")
    message = advert("w-1", "Weather", "I forecast.", [])
    assert not signer.verify(message)  # unsigned
    signed = signer.sign(message)
    assert signer.verify(signed)
    tampered = signed.model_copy(update={"sender": "evil"})
    assert not signer.verify(tampered)
    assert not Signer("other").verify(signed)
    with pytest.raises(ValueError):
        Signer("")


def test_codec_roundtrip():
    message = Signer("s").sign(request("c", "C", "w", "rid", "cap", {"n": 1, "text": "héllo"}))
    assert decode(encode(message)) == message


def test_decode_rejects_garbage_and_other_protocols():
    with pytest.raises(DecodeError):
        decode(b"hello")
    other = json.loads(encode(advert("w", "W", "", [])))
    other["protocol"] = "loquor-broadcast/0.1"
    with pytest.raises(DecodeError, match="unsupported protocol"):
        decode(json.dumps(other).encode())


def test_size_cap_is_transport_independent():
    assert MAX_MESSAGE_SIZE == 4096
