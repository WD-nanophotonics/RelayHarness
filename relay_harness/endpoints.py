"""Backend-neutral Agent endpoints, activations, and durable registration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .errors import IntegrityError, SchemaError
from .model_policy import ModelEvidence, ModelRequest
from .schemas import PROTOCOL_VERSION, new_id, utc_now
from .storage import atomic_create_json, atomic_write_json, read_json


BACKEND_TYPES = {"subprocess", "codex_thread", "future_backend"}
ROLES = {"worker", "coordinator"}


@dataclass
class AgentEndpoint:
    endpoint_id: str
    backend_type: str
    role: str
    project_id: str
    run_id: str | None = None
    external_id: str | None = None
    registered_at: str = field(default_factory=utc_now)
    binding_metadata: dict[str, Any] = field(default_factory=dict)
    backend_evidence: dict[str, Any] = field(default_factory=dict)
    last_verified_state: str = "unknown"
    last_verified_at: str | None = None
    protocol_version: str = PROTOCOL_VERSION

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise SchemaError("unsupported endpoint protocol")
        if not self.endpoint_id or self.backend_type not in BACKEND_TYPES or self.role not in ROLES or not self.project_id:
            raise SchemaError("invalid AgentEndpoint identity")
        if self.backend_type == "codex_thread" and not self.external_id:
            raise SchemaError("codex_thread endpoint requires an external thread/task identifier")
        if self.backend_type == "subprocess" and self.external_id is not None and not str(self.external_id):
            raise SchemaError("subprocess external identity must be non-empty when present")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentEndpoint":
        try:
            result = cls(**data)
        except TypeError as exc:
            raise SchemaError(f"invalid AgentEndpoint: {exc}") from exc
        result.validate()
        return result


@dataclass
class ExecutionIdentity:
    run_id: str
    turn_id: str
    activation_id: str
    endpoint_id: str
    role: str
    backend_type: str
    external_execution_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if not all([self.run_id, self.turn_id, self.activation_id, self.endpoint_id, self.role, self.backend_type]):
            raise SchemaError("execution identity has missing fields")
        if self.role not in ROLES or self.backend_type not in BACKEND_TYPES:
            raise SchemaError("execution identity has invalid role/backend")

    @classmethod
    def new(cls, run_id: str, turn_id: str, endpoint: AgentEndpoint, external_execution_id: str | None = None) -> "ExecutionIdentity":
        return cls(run_id, turn_id, new_id("activation"), endpoint.endpoint_id, endpoint.role, endpoint.backend_type, external_execution_id)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionIdentity":
        try:
            result = cls(**data)
        except TypeError as exc:
            raise SchemaError(f"invalid ExecutionIdentity: {exc}") from exc
        result.validate()
        return result


@dataclass
class ActivationRecord:
    identity: ExecutionIdentity
    state: str
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    backend_evidence: dict[str, Any] = field(default_factory=dict)
    ack_verified: bool = False
    model_evidence: dict[str, Any] | None = None

    def validate(self) -> None:
        self.identity.validate()
        if self.state not in {"starting", "active", "completed", "failed", "waiting"}:
            raise SchemaError("invalid activation state")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"identity": self.identity.to_dict(), **{k: v for k, v in self.__dict__.items() if k != "identity"}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActivationRecord":
        values = dict(data)
        try:
            identity = ExecutionIdentity.from_dict(values.pop("identity"))
            result = cls(identity=identity, **values)
        except (KeyError, TypeError) as exc:
            raise SchemaError(f"invalid ActivationRecord: {exc}") from exc
        result.validate()
        return result


class EndpointRegistry:
    """Durable role-to-endpoint registration; endpoint identity is not a PID."""

    def __init__(self, root: Path):
        self.root = root
        self.endpoints = root / "endpoints"
        self.activations = root / "activations"
        self.endpoints.mkdir(parents=True, exist_ok=True)
        self.activations.mkdir(parents=True, exist_ok=True)

    def register(self, endpoint: AgentEndpoint) -> Path:
        endpoint.validate()
        target = self.endpoints / f"{endpoint.role}.json"
        if target.exists():
            existing = AgentEndpoint.from_dict(read_json(target))
            if existing.endpoint_id != endpoint.endpoint_id or existing.backend_type != endpoint.backend_type:
                raise IntegrityError(f"role already registered to another endpoint: {endpoint.role}")
            atomic_write_json(target, endpoint.to_dict())
            return target
        atomic_create_json(target, endpoint.to_dict())
        return target

    def get(self, role: str) -> AgentEndpoint:
        if role not in ROLES:
            raise ValueError("invalid endpoint role")
        return AgentEndpoint.from_dict(read_json(self.endpoints / f"{role}.json"))

    def list(self) -> list[AgentEndpoint]:
        return [AgentEndpoint.from_dict(read_json(path)) for path in sorted(self.endpoints.glob("*.json"))]

    def record_activation(self, activation: ActivationRecord) -> Path:
        activation.validate()
        target = self.activations / f"{activation.identity.activation_id}.json"
        atomic_create_json(target, activation.to_dict())
        return target

    def update_activation(self, activation: ActivationRecord) -> Path:
        activation.validate()
        target = self.activations / f"{activation.identity.activation_id}.json"
        if not target.exists():
            raise FileNotFoundError(target)
        atomic_write_json(target, activation.to_dict())
        return target

    def latest_activation(self, role: str) -> ActivationRecord | None:
        records = []
        for path in self.activations.glob("*.json"):
            record = ActivationRecord.from_dict(read_json(path))
            if record.identity.role == role:
                records.append(record)
        return max(records, key=lambda item: item.started_at) if records else None


class CodexThreadControl(Protocol):
    """Host adapter for the locally available Codex task/thread control surface."""

    def send_follow_up(self, thread_id: str, prompt: str, model: str | None, thinking: str | None) -> str: ...

    def inspect(self, thread_id: str) -> dict[str, Any]: ...

    def wait(self, thread_id: str) -> dict[str, Any]: ...

    def read(self, thread_id: str) -> dict[str, Any]: ...


class AgentBackend(ABC):
    @abstractmethod
    def bind(self, endpoint: AgentEndpoint) -> AgentEndpoint: ...

    @abstractmethod
    def provision(self, role: str, project_id: str) -> AgentEndpoint: ...

    @abstractmethod
    def wake_or_resume(self, endpoint: AgentEndpoint, identity: ExecutionIdentity, capsule_ref: str) -> ActivationRecord: ...

    @abstractmethod
    def inspect_status(self, endpoint: AgentEndpoint) -> dict[str, Any]: ...

    @abstractmethod
    def verify_identity(self, endpoint: AgentEndpoint, evidence: dict[str, Any]) -> None: ...

    @abstractmethod
    def verify_model(self, request: ModelRequest, evidence: ModelEvidence) -> None: ...

    @abstractmethod
    def capture_backend_evidence(self, endpoint: AgentEndpoint) -> dict[str, Any]: ...
