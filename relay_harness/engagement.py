"""Product-level enrollment and engagement above the durable mailbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import InstallationConfig, ProjectProfile
from .endpoints import ActivationRecord, AgentEndpoint, EndpointRegistry, ExecutionIdentity
from .runtime import RunPaths, RuntimeLayout
from .schemas import OwnershipRecord
from .storage import atomic_write_json, read_json


@dataclass
class EngagementResult:
    run_id: str
    worker_endpoint_id: str
    coordinator_endpoint_id: str | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class EngagementService:
    """Enrolls an already-existing Agent endpoint without target-repository code."""

    def __init__(self, profile: ProjectProfile, installation: InstallationConfig | None = None):
        self.profile = profile
        self.installation = installation or InstallationConfig(runtime_root=profile.runtime.root)
        machine_root = None if self.installation.runtime_root == ".relayharness" else self.installation.runtime_root
        self.layout = RuntimeLayout(profile, machine_root)
        self.registry: EndpointRegistry = self.layout.endpoint_registry()

    def enroll_current_worker(self, worker: AgentEndpoint, objective_ref: str | None = None) -> tuple[RunPaths, EngagementResult]:
        if worker.role != "worker" or worker.project_id != self.profile.project_id:
            raise ValueError("enrolled endpoint must be the current project's worker")
        self.registry.register(worker)
        paths = self.layout.new_run(objective_ref)
        manifest = read_json(paths.manifest)
        manifest.update({"status": "awaiting_coordinator", "worker_endpoint_id": worker.endpoint_id})
        atomic_write_json(paths.manifest, manifest)
        identity = ExecutionIdentity.new(paths.root.name, "enrollment", worker)
        self.registry.record_activation(ActivationRecord(identity, "active", ack_verified=True, backend_evidence={"enrollment": "existing_endpoint"}))
        self.layout.write_ownership(paths, OwnershipRecord(
            run_id=paths.root.name, turn_id="enrollment", current_role="worker", message_id=None,
            owner_pid=None, state="owned", endpoint_id=worker.endpoint_id, activation_id=identity.activation_id,
            backend_type=worker.backend_type,
        ))
        return paths, EngagementResult(paths.root.name, worker.endpoint_id, None, "awaiting_coordinator")

    def bind_coordinator(self, paths: RunPaths, coordinator: AgentEndpoint) -> EngagementResult:
        if coordinator.role != "coordinator" or coordinator.project_id != self.profile.project_id:
            raise ValueError("endpoint must be the current project's coordinator")
        self.registry.register(coordinator)
        manifest = read_json(paths.manifest)
        manifest.update({"status": "ready", "coordinator_endpoint_id": coordinator.endpoint_id})
        atomic_write_json(paths.manifest, manifest)
        return EngagementResult(paths.root.name, manifest["worker_endpoint_id"], coordinator.endpoint_id, "ready")
