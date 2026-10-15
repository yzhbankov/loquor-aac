"""Wire format: one ``Message`` envelope, four message types.

Every message is a JSON object with the same envelope; ``type`` selects the
payload::

    advert    recipient=None   {name, description, capabilities: [tag, ...]}
    request   recipient=uid    {request_id, capability, input: {...}}
    response  recipient=uid    {request_id, output: {...}}
    error     recipient=uid    {request_id, code, detail}

Agents are addressed by **uid** inside the message, never by a transport
address, so the same message means the same thing on every transport.
Payloads are structured dicts: free text is one kind of input (for example
``{"question": "..."}``), not the only kind.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "loquor/1"
# Independent of any transport: a message must fit the smallest link we
# intend to support (BLE after chunking, LoRa-class radios).
MAX_MESSAGE_SIZE = 4096


class MessageType(str, Enum):
    ADVERT = "advert"
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"


class Message(BaseModel):
    protocol: str = PROTOCOL_VERSION
    type: MessageType
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sender: str  # uid of the sending agent
    sender_name: str = ""
    recipient: str | None = None  # uid of the addressee; None = everyone
    payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: float = Field(default_factory=time.time)
    signature: str | None = None

    @property
    def request_id(self) -> str:
        return str(self.payload.get("request_id", ""))


def advert(sender: str, sender_name: str, description: str, capabilities: list[str]) -> Message:
    return Message(
        type=MessageType.ADVERT,
        sender=sender,
        sender_name=sender_name,
        payload={"name": sender_name, "description": description, "capabilities": capabilities},
    )


def request(
    sender: str, sender_name: str, recipient: str, request_id: str, capability: str, input: dict
) -> Message:
    return Message(
        type=MessageType.REQUEST,
        sender=sender,
        sender_name=sender_name,
        recipient=recipient,
        payload={"request_id": request_id, "capability": capability, "input": input},
    )


def response(sender: str, sender_name: str, recipient: str, request_id: str, output: dict) -> Message:
    return Message(
        type=MessageType.RESPONSE,
        sender=sender,
        sender_name=sender_name,
        recipient=recipient,
        payload={"request_id": request_id, "output": output},
    )


def error(
    sender: str, sender_name: str, recipient: str, request_id: str, code: str, detail: str
) -> Message:
    return Message(
        type=MessageType.ERROR,
        sender=sender,
        sender_name=sender_name,
        recipient=recipient,
        payload={"request_id": request_id, "code": code, "detail": detail},
    )
