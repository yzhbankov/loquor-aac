"""Structured message types exchanged between agents.

Agents do not exchange free-form natural-language conversations by default.
They exchange a small set of validated, structured message types; an LLM may
interpret evidence and propose hypotheses, but deterministic application
logic controls task states, retries, permissions, and final output.

The task/capability vocabulary borrows concepts from Google's A2A protocol
(tasks, capabilities, structured payloads) rather than inventing a new
semantic standard.
"""

from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "loquor/0.1"


class MessageType(str, Enum):
    """Every message on the wire is one of these types."""

    TASK_REQUEST = "task_request"  # start a task on a peer (capability + data)
    INFO_REQUEST = "info_request"  # peer needs more evidence to continue
    INFO_RESPONSE = "info_response"  # answer to an info_request
    TASK_RESULT = "task_result"  # final structured result for a task
    ERROR = "error"  # validation/authorization/processing failure


class TaskState(str, Enum):
    """Lifecycle of a task as tracked in the persistent store."""

    PENDING = "pending"  # created locally, not yet sent/accepted
    IN_PROGRESS = "in_progress"  # accepted, work ongoing
    AWAITING_INFO = "awaiting_info"  # blocked on an info_request round-trip
    INTERRUPTED = "interrupted"  # peer unreachable; resumable on reconnect
    COMPLETED = "completed"  # final result produced and stored
    FAILED = "failed"  # unrecoverable error


class Envelope(BaseModel):
    """The single wire format. Payload semantics depend on ``type``.

    ``signature`` is an HMAC-SHA256 over the canonical JSON of all other
    fields, keyed by the pairing secret shared between authenticated peers
    (see :mod:`loquor.identity`).
    """

    protocol: str = PROTOCOL_VERSION
    type: MessageType
    task_id: str
    sender: str
    capability: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: float = Field(default_factory=time.time)
    signature: str | None = None

    def signing_bytes(self) -> bytes:
        """Canonical byte representation of everything except the signature."""
        data = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def task_request(
    sender: str, capability: str, observations: dict[str, Any], task_id: str | None = None
) -> Envelope:
    return Envelope(
        type=MessageType.TASK_REQUEST,
        task_id=task_id or new_task_id(),
        sender=sender,
        capability=capability,
        payload={"observations": observations},
    )


def info_request(sender: str, task_id: str, needed: list[str], reason: str) -> Envelope:
    return Envelope(
        type=MessageType.INFO_REQUEST,
        task_id=task_id,
        sender=sender,
        payload={"needed": needed, "reason": reason},
    )


def info_response(sender: str, task_id: str, measurements: dict[str, Any]) -> Envelope:
    return Envelope(
        type=MessageType.INFO_RESPONSE,
        task_id=task_id,
        sender=sender,
        payload={"measurements": measurements},
    )


def task_result(sender: str, task_id: str, result: dict[str, Any]) -> Envelope:
    return Envelope(
        type=MessageType.TASK_RESULT,
        task_id=task_id,
        sender=sender,
        payload={"result": result},
    )


def error(sender: str, task_id: str, code: str, detail: str) -> Envelope:
    return Envelope(
        type=MessageType.ERROR,
        task_id=task_id,
        sender=sender,
        payload={"code": code, "detail": detail},
    )
