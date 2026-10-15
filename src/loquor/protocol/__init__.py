"""The loquor protocol: one message format, signed, carried by any transport.

- :mod:`message` — the ``Message`` envelope and its four types
- :mod:`signing` — HMAC signing and verification with the pairing secret
- :mod:`codec`   — ``Message`` <-> bytes
"""

from loquor.protocol.codec import DecodeError, decode, encode
from loquor.protocol.message import (
    MAX_MESSAGE_SIZE,
    PROTOCOL_VERSION,
    Message,
    MessageType,
    advert,
    error,
    request,
    response,
)
from loquor.protocol.signing import Signer

__all__ = [
    "MAX_MESSAGE_SIZE",
    "PROTOCOL_VERSION",
    "DecodeError",
    "Message",
    "MessageType",
    "Signer",
    "advert",
    "decode",
    "encode",
    "error",
    "request",
    "response",
]
