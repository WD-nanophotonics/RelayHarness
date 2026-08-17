"""Durable capsules and ownership records.

These objects contain references to larger files rather than recursively copying prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import uuid4

from .errors import SchemaError
from .model_policy import REQUIRED_MODEL, REQUIRED_REASONING
from .storage import canonical_json, sha256_bytes


PROTOCOL_VERSION = "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class BootstrapCapsule:
    project_id: str
    run_id: str
    turn_id: str
    role: str
    runtime_root: str
    profile_ref: str
    durable_state_refs: dict[str, str]
    semantic_ref: str | None
    repository: dict[str, str | None]
    expected_git: dict[str, str | None]
    output_contract: dict[str, Any]
    logging_contract: dict[str, Any]
    next_role: str | None
    terminal_contract: dict[str, Any]
    required_model: str = REQUIRED_MODEL
    required_reasoning: str = REQUIRED_REASONING
    protocol_version: str = PROTOCOL_VERSION
    capsule_id: str = field(default_factory=lambda: new_id("capsule"))
    created_at: str = field(default_factory=utc_now)

    kind: ClassVar[str] = "bootstrap"

    def validate(self) -> None:
        required = [self.project_id, self.run_id, self.turn_id, self.role, self.runtime_root, self.profile_ref]
        if any(not item for item in required):
            raise SchemaError("bootstrap capsule has missing identity fields")
        if self.protocol_version != PROTOCOL_VERSION:
            raise SchemaError(f"unsupported capsule protocol: {self.protocol_version}")
        if self.required_model != REQUIRED_MODEL or self.required_reasoning != REQUIRED_REASONING:
            raise SchemaError("bootstrap capsule must require Luna High")
        if not self.output_contract or not self.logging_contract or not self.terminal_contract:
            raise SchemaError("bootstrap capsule contracts must be explicit")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"kind": self.kind, **self.__dict__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BootstrapCapsule":
        values = dict(data)
        values.pop("kind", None)
        try:
            result = cls(**values)
        except TypeError as exc:
            raise SchemaError(f"invalid bootstrap capsule: {exc}") from exc
        result.validate()
        return result


@dataclass
class TaskCapsule:
    project_id: str
    run_id: str
    turn_id: str
    owner_role: str
    objective_ref: str
    acceptance_ref: str | None
    input_refs: list[str] = field(default_factory=list)
    task_id: str = field(default_factory=lambda: new_id("task"))
    created_at: str = field(default_factory=utc_now)
    protocol_version: str = PROTOCOL_VERSION

    kind: ClassVar[str] = "task"

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION or not all([self.project_id, self.run_id, self.turn_id, self.owner_role, self.objective_ref]):
            raise SchemaError("invalid task capsule identity")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"kind": self.kind, **self.__dict__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskCapsule":
        values = dict(data)
        values.pop("kind", None)
        try:
            result = cls(**values)
        except TypeError as exc:
            raise SchemaError(f"invalid task capsule: {exc}") from exc
        result.validate()
        return result


@dataclass
class ResultCapsule:
    project_id: str
    run_id: str
    turn_id: str
    task_id: str
    owner_role: str
    status: str
    summary_ref: str
    artifact_refs: list[str] = field(default_factory=list)
    concern_refs: list[str] = field(default_factory=list)
    result_id: str = field(default_factory=lambda: new_id("result"))
    created_at: str = field(default_factory=utc_now)
    protocol_version: str = PROTOCOL_VERSION

    kind: ClassVar[str] = "result"

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION or self.status not in {"completed", "blocked", "failed", "aborted"}:
            raise SchemaError("invalid result capsule")
        if not all([self.project_id, self.run_id, self.turn_id, self.task_id, self.owner_role, self.summary_ref]):
            raise SchemaError("result capsule has missing identity or summary reference")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"kind": self.kind, **self.__dict__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResultCapsule":
        values = dict(data)
        values.pop("kind", None)
        try:
            result = cls(**values)
        except TypeError as exc:
            raise SchemaError(f"invalid result capsule: {exc}") from exc
        result.validate()
        return result


@dataclass
class Claim:
    resource_type: str
    resource_id: str
    owner_role: str
    owner_pid: int | None
    turn_id: str
    owner_endpoint_id: str | None = None
    owner_activation_id: str | None = None
    owner_backend_type: str | None = None
    acquired_at: str = field(default_factory=utc_now)
    released_at: str | None = None

    def validate(self) -> None:
        if not all([self.resource_type, self.resource_id, self.owner_role, self.turn_id]):
            raise SchemaError("claim has missing identity fields")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        try:
            result = cls(**data)
        except TypeError as exc:
            raise SchemaError(f"invalid claim: {exc}") from exc
        result.validate()
        return result


@dataclass
class MailboxMessage:
    """Small immutable mailbox metadata; semantic content lives at payload_ref."""

    message_id: str
    run_id: str
    turn_id: str
    sender_role: str
    recipient_role: str
    message_kind: str
    payload_ref: str
    capsule_ref: str
    payload_sha256: str
    parent_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    protocol_version: str = PROTOCOL_VERSION
    message_sha256: str = ""

    kind: ClassVar[str] = "mailbox_message"

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message_id": self.message_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "sender_role": self.sender_role,
            "recipient_role": self.recipient_role,
            "message_kind": self.message_kind,
            "payload_ref": self.payload_ref,
            "capsule_ref": self.capsule_ref,
            "payload_sha256": self.payload_sha256,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "protocol_version": self.protocol_version,
        }

    def seal(self) -> "MailboxMessage":
        self.message_sha256 = sha256_bytes(canonical_json(self.unsigned_dict()))
        return self

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise SchemaError("unsupported mailbox message protocol")
        if not all([self.message_id, self.run_id, self.turn_id, self.sender_role, self.recipient_role, self.message_kind, self.payload_ref, self.capsule_ref, self.payload_sha256]):
            raise SchemaError("mailbox message has missing fields")
        if self.sender_role == self.recipient_role or self.recipient_role not in {"relay", "coordinator", "worker"}:
            raise SchemaError("mailbox message has invalid sender/recipient roles")
        if len(self.payload_sha256) != 64:
            raise SchemaError("mailbox message payload hash is not SHA-256")
        expected = sha256_bytes(canonical_json(self.unsigned_dict()))
        if not self.message_sha256 or self.message_sha256 != expected:
            raise SchemaError("mailbox message integrity hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        self.seal()
        self.validate()
        return {**self.unsigned_dict(), "message_sha256": self.message_sha256}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MailboxMessage":
        values = dict(data)
        values.pop("kind", None)
        try:
            result = cls(**values)
        except TypeError as exc:
            raise SchemaError(f"invalid mailbox message: {exc}") from exc
        result.validate()
        return result


@dataclass
class OwnershipRecord:
    run_id: str
    turn_id: str
    current_role: str
    message_id: str | None
    owner_pid: int | None
    state: str
    successor_role: str | None = None
    predecessor_role: str | None = None
    endpoint_id: str | None = None
    activation_id: str | None = None
    backend_type: str | None = None
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.state not in {"owned", "handoff_pending", "transferred", "terminal"}:
            raise SchemaError("invalid ownership state")
        if not all([self.run_id, self.turn_id, self.current_role]):
            raise SchemaError("ownership record has missing identity")
        if self.state == "handoff_pending" and not self.successor_role:
            raise SchemaError("pending handoff must identify successor role")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OwnershipRecord":
        try:
            result = cls(**data)
        except TypeError as exc:
            raise SchemaError(f"invalid ownership record: {exc}") from exc
        result.validate()
        return result
