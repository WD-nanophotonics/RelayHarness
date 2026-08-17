"""Mechanical contract for host control that is exposed only to an Agent.

The local Codex app exposes task/thread operations to the calling Agent, not to
ordinary deterministic Python.  This module keeps the Kernel authoritative by
generating an exact request and validating the returned mechanical receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import IntegrityError, SchemaError
from .schemas import new_id, utc_now
from .storage import sha256_file


@dataclass(frozen=True)
class HostBridgeRequest:
    """A Kernel-authorized, non-semantic host operation."""

    operation: str
    endpoint_id: str
    thread_id: str
    activation_id: str
    wakeup_text: str
    provider_model_id: str
    reasoning: str
    request_id: str = field(default_factory=lambda: new_id("host_request"))
    expected_evidence: tuple[str, ...] = ("thread_id", "submission_id", "accepted")

    def validate(self) -> None:
        if self.operation not in {"send_follow_up", "inspect", "wait", "read"}:
            raise SchemaError("unsupported host bridge operation")
        if not all((self.request_id, self.endpoint_id, self.thread_id, self.activation_id, self.wakeup_text)):
            raise SchemaError("host bridge request has missing identity")
        expected = f"RelayHarness activation {self.activation_id}. Read bootstrap capsule: "
        if not self.wakeup_text.startswith(expected) or "\n" in self.wakeup_text:
            raise IntegrityError("host bridge wakeup is not bootstrap-only")
        if not self.provider_model_id or not self.reasoning:
            raise SchemaError("host bridge request is missing model settings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {**self.__dict__, "expected_evidence": list(self.expected_evidence)}


@dataclass(frozen=True)
class HostBridgeReceipt:
    """Objective evidence returned after the Agent performs one host operation."""

    request_id: str
    operation: str
    endpoint_id: str
    thread_id: str
    activation_id: str
    wakeup_text: str
    accepted: bool
    submission_id: str | None
    evidence: dict[str, Any]
    returned_at: str = field(default_factory=utc_now)
    error: str | None = None

    def validate_for(self, request: HostBridgeRequest) -> None:
        request.validate()
        if self.request_id != request.request_id or self.operation != request.operation:
            raise IntegrityError("host bridge receipt request mismatch")
        if self.endpoint_id != request.endpoint_id or self.thread_id != request.thread_id:
            raise IntegrityError("host bridge receipt target mismatch")
        if self.activation_id != request.activation_id or self.wakeup_text != request.wakeup_text:
            raise IntegrityError("host bridge receipt activation/wakeup mismatch")
        if not self.accepted or not self.submission_id:
            raise IntegrityError(self.error or "host bridge operation was not accepted")
        observed_thread = self.evidence.get("thread_id") or self.evidence.get("task_id")
        if observed_thread is not None and observed_thread != request.thread_id:
            raise IntegrityError("host bridge evidence target mismatch")
        if self.evidence.get("submission_id") not in {None, self.submission_id}:
            raise IntegrityError("host bridge evidence submission mismatch")

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ActivationAck:
    """Durable acknowledgement emitted by the activated semantic Agent."""

    activation_id: str
    endpoint_id: str
    role: str
    run_id: str
    turn_id: str
    capsule_id: str
    capsule_ref: str
    capsule_sha256: str
    acknowledged_at: str = field(default_factory=utc_now)

    def validate_against(self, expected: dict[str, str], capsule_path: Path) -> None:
        for field_name in ("activation_id", "endpoint_id", "role", "run_id", "turn_id"):
            if getattr(self, field_name) != expected.get(field_name):
                raise IntegrityError(f"activation ACK {field_name} mismatch")
        if self.capsule_ref != expected.get("capsule_ref"):
            raise IntegrityError("activation ACK capsule reference mismatch")
        if self.capsule_id != expected.get("capsule_id"):
            raise IntegrityError("activation ACK capsule identity mismatch")
        if not capsule_path.is_file() or sha256_file(capsule_path) != self.capsule_sha256:
            raise IntegrityError("activation ACK capsule hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()
