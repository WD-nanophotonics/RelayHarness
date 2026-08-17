"""Generic project profile schema and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SchemaError
from .model_policy import REQUIRED_MODEL, REQUIRED_REASONING
from .storage import atomic_write_json, read_json


SCHEMA_VERSION = "1"


@dataclass
class RepositoryConfig:
    path: str
    branch: str | None = None


@dataclass
class AgentConfig:
    command: list[str] = field(default_factory=list)
    model: str = REQUIRED_MODEL
    reasoning: str = REQUIRED_REASONING
    backend: str = "subprocess"
    provider_model_id: str | None = None
    backend_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    root: str = ".relayharness"


@dataclass
class TransportConfig:
    adapter: str = "manual"
    external_id: str | None = None


@dataclass
class PolicyConfig:
    mode: str = "bounded"
    safe_stop: bool = True
    max_turns: int | None = None
    git_cleanliness: str = "observe"
    push_required: bool = False
    safety_constraints: list[str] = field(default_factory=list)


@dataclass
class ProjectProfile:
    project_id: str
    repository: RepositoryConfig
    agent: AgentConfig
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    supervisory_transport: TransportConfig = field(default_factory=TransportConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    endpoint_overrides: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported profile schema version: {self.schema_version}")
        if not self.project_id or "/" in self.project_id or "\\" in self.project_id:
            raise SchemaError("project_id must be a non-empty path-safe identifier")
        if not self.repository.path:
            raise SchemaError("repository.path is required")
        if self.agent.backend not in {"subprocess", "codex_thread", "future_backend"}:
            raise SchemaError("agent.backend must be subprocess, codex_thread, or future_backend")
        if self.agent.backend == "subprocess" and not self.agent.command:
            raise SchemaError("subprocess agent.command must not be empty")
        if self.agent.model != REQUIRED_MODEL or self.agent.reasoning != REQUIRED_REASONING:
            raise SchemaError("agent must explicitly request Luna with High reasoning")
        if self.policy.mode not in {"bounded", "continuous"}:
            raise SchemaError("policy.mode must be bounded or continuous")
        if self.policy.max_turns is not None and self.policy.max_turns < 1:
            raise SchemaError("policy.max_turns must be positive when set")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "repository": {"path": self.repository.path, "branch": self.repository.branch},
            "agent": {
                "command": self.agent.command,
                "model": self.agent.model,
                "reasoning": self.agent.reasoning,
                "backend": self.agent.backend,
                "provider_model_id": self.agent.provider_model_id,
                "backend_options": self.agent.backend_options,
            },
            "runtime": {"root": self.runtime.root},
            "supervisory_transport": {"adapter": self.supervisory_transport.adapter, "external_id": self.supervisory_transport.external_id},
            "policy": {
                "mode": self.policy.mode,
                "safe_stop": self.policy.safe_stop,
                "max_turns": self.policy.max_turns,
                "git_cleanliness": self.policy.git_cleanliness,
                "push_required": self.policy.push_required,
                "safety_constraints": self.policy.safety_constraints,
            },
            "endpoint_overrides": self.endpoint_overrides,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectProfile":
        try:
            profile = cls(
                schema_version=data.get("schema_version", SCHEMA_VERSION),
                project_id=data["project_id"],
                repository=RepositoryConfig(**data["repository"]),
                agent=AgentConfig(**data["agent"]),
                runtime=RuntimeConfig(**data.get("runtime", {})),
                supervisory_transport=TransportConfig(**data.get("supervisory_transport", {})),
                policy=PolicyConfig(**data.get("policy", {})),
                endpoint_overrides=data.get("endpoint_overrides", {}),
            )
        except (KeyError, TypeError) as exc:
            raise SchemaError(f"invalid project profile: {exc}") from exc
        profile.validate()
        return profile

    @classmethod
    def load(cls, path: Path) -> "ProjectProfile":
        return cls.from_dict(read_json(path))

    def save(self, path: Path) -> None:
        atomic_write_json(path, self.to_dict())


@dataclass
class InstallationConfig:
    """Reusable machine/product configuration, separate from project semantics."""

    config_version: str = "1"
    runtime_root: str = ".relayharness"
    coordinator_backend: str = "codex_thread"
    coordinator_endpoint_id: str | None = None
    provider_models: dict[str, str] = field(default_factory=lambda: {REQUIRED_MODEL: "gpt-5.6-luna"})
    supervisory_transport_defaults: dict[str, Any] = field(default_factory=lambda: {"adapter": "manual"})
    backend_options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default_path(cls) -> Path:
        return Path.home() / ".relayharness" / "config.json"

    def validate(self) -> None:
        if self.config_version != "1":
            raise SchemaError(f"unsupported installation config version: {self.config_version}")
        if self.coordinator_backend not in {"subprocess", "codex_thread", "future_backend"}:
            raise SchemaError("invalid coordinator backend")
        if self.provider_models.get(REQUIRED_MODEL) in {None, ""}:
            raise SchemaError("Luna provider model mapping is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstallationConfig":
        try:
            result = cls(**data)
        except TypeError as exc:
            raise SchemaError(f"invalid installation config: {exc}") from exc
        result.validate()
        return result

    @classmethod
    def load(cls, path: Path) -> "InstallationConfig":
        return cls.from_dict(read_json(path))

    def save(self, path: Path) -> None:
        atomic_write_json(path, self.to_dict())
