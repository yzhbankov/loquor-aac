"""``Message`` <-> bytes.

One codec (JSON) today. The boundary exists so that a compact encoding for
tiny radios, or an encrypted one, is a second implementation of these two
functions rather than a change anywhere else.
"""

from __future__ import annotations

from pydantic import ValidationError

from loquor.protocol.message import PROTOCOL_VERSION, Message


class DecodeError(ValueError):
    """The bytes are not a loquor message this agent understands."""


def encode(message: Message) -> bytes:
    return message.model_dump_json().encode()


def decode(data: bytes) -> Message:
    try:
        message = Message.model_validate_json(data)
    except ValidationError as exc:
        raise DecodeError("not a loquor message") from exc
    if message.protocol != PROTOCOL_VERSION:
        raise DecodeError(f"unsupported protocol {message.protocol!r}")
    return message
