"""Concrete endpoint backends kept behind the backend-neutral AgentBackend API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import InstallationConfig, ProjectProfile
from .endpoints import AgentBackend, AgentEndpoint, ActivationRecord, CodexThreadControl, ExecutionIdentity
from .errors import BackendUnavailable, IntegrityError
from .host_bridge import HostBridgeReceipt, HostBridgeRequest
from .model_policy import ModelEvidence, ModelPolicy, ModelRequest
from .process import AgentLauncher, LaunchAuthority
from .schemas import BootstrapCapsule
from .storage import read_json


class SubprocessBackend(AgentBackend):
    def __init__(self, profile: ProjectProfile, launcher: AgentLauncher):
        self.profile = profile
        self.launcher = launcher

    def bind(self, endpoint: AgentEndpoint) -> AgentEndpoint:
        endpoint.validate()
        if endpoint.backend_type != "subprocess":
            raise IntegrityError("SubprocessBackend cannot bind another backend type")
        return endpoint

    def provision(self, role: str, project_id: str) -> AgentEndpoint:
        from .schemas import new_id
        return AgentEndpoint(new_id(f"{role}_endpoint"), "subprocess", role, project_id)

    def wake_or_resume(self, endpoint: AgentEndpoint, identity: ExecutionIdentity, capsule_ref: str) -> ActivationRecord:
        capsule_path = Path(capsule_ref)
        capsule = BootstrapCapsule.from_dict(read_json(capsule_path))
        spec = LaunchAuthority(self.profile).build_spec(capsule, capsule_path)
        handle = self.launcher.launch(spec)
        identity.external_execution_id = str(handle.pid)
        return ActivationRecord(identity, "active", backend_evidence={"pid": handle.pid, "started_at": handle.started_at})

    def inspect_status(self, endpoint: AgentEndpoint) -> dict[str, Any]:
        return {"backend": "subprocess", "endpoint_id": endpoint.endpoint_id, "state": endpoint.last_verified_state}

    def verify_identity(self, endpoint: AgentEndpoint, evidence: dict[str, Any]) -> None:
        if endpoint.external_id and str(evidence.get("pid")) != str(endpoint.external_id):
            raise IntegrityError("subprocess PID evidence does not match endpoint")

    def verify_model(self, request: ModelRequest, evidence: ModelEvidence) -> None:
        ModelPolicy.validate_request(request)
        ModelPolicy.validate_evidence(evidence)

    def capture_backend_evidence(self, endpoint: AgentEndpoint) -> dict[str, Any]:
        return {"backend_type": "subprocess", "external_id": endpoint.external_id}


class CodexThreadBackend(AgentBackend):
    """Codex task/thread adapter using the host's proven follow-up/inspect surface."""

    def __init__(self, profile: ProjectProfile, control: CodexThreadControl, installation: InstallationConfig | None = None):
        self.profile = profile
        self.control = control
        self.installation = installation or InstallationConfig()

    @staticmethod
    def wakeup_text(identity: ExecutionIdentity, capsule_ref: str) -> str:
        return f"RelayHarness activation {identity.activation_id}. Read bootstrap capsule: {capsule_ref}"

    def bind(self, endpoint: AgentEndpoint) -> AgentEndpoint:
        endpoint.validate()
        if endpoint.backend_type != "codex_thread":
            raise IntegrityError("CodexThreadBackend cannot bind another backend type")
        return endpoint

    def provision(self, role: str, project_id: str) -> AgentEndpoint:
        provision = getattr(self.control, "provision", None)
        if provision is None:
            raise BackendUnavailable("current Codex host adapter exposes no thread provisioning operation")
        return provision(role, project_id)

    def wake_or_resume(self, endpoint: AgentEndpoint, identity: ExecutionIdentity, capsule_ref: str) -> ActivationRecord:
        endpoint = self.bind(endpoint)
        provider_model = self.installation.provider_models.get(self.profile.agent.model)
        if not provider_model:
            raise BackendUnavailable(f"no provider model mapping for logical model {self.profile.agent.model}")
        submission_id = self.control.send_follow_up(endpoint.external_id, self.wakeup_text(identity, capsule_ref), provider_model, self.profile.agent.reasoning)
        identity.external_execution_id = submission_id
        return ActivationRecord(identity, "starting", backend_evidence={"thread_id": endpoint.external_id, "submission_id": submission_id, "wakeup": self.wakeup_text(identity, capsule_ref)})

    def build_host_bridge_request(self, endpoint: AgentEndpoint, identity: ExecutionIdentity, capsule_ref: str) -> HostBridgeRequest:
        endpoint = self.bind(endpoint)
        provider_model = self.installation.provider_models.get(self.profile.agent.model)
        if not provider_model:
            raise BackendUnavailable(f"no provider model mapping for logical model {self.profile.agent.model}")
        request = HostBridgeRequest(
            operation="send_follow_up",
            endpoint_id=endpoint.endpoint_id,
            thread_id=str(endpoint.external_id),
            activation_id=identity.activation_id,
            wakeup_text=self.wakeup_text(identity, capsule_ref),
            provider_model_id=provider_model,
            reasoning=self.profile.agent.reasoning,
        )
        request.validate()
        return request

    def accept_host_bridge_receipt(
        self,
        endpoint: AgentEndpoint,
        identity: ExecutionIdentity,
        request: HostBridgeRequest,
        receipt: HostBridgeReceipt,
    ) -> ActivationRecord:
        endpoint = self.bind(endpoint)
        if request.endpoint_id != endpoint.endpoint_id or request.thread_id != endpoint.external_id:
            raise IntegrityError("host bridge request endpoint mismatch")
        if request.activation_id != identity.activation_id:
            raise IntegrityError("host bridge request activation mismatch")
        receipt.validate_for(request)
        identity.external_execution_id = receipt.submission_id
        return ActivationRecord(
            identity,
            "starting",
            backend_evidence={
                "thread_id": endpoint.external_id,
                "submission_id": receipt.submission_id,
                "request_id": request.request_id,
                "wakeup": request.wakeup_text,
                "host_evidence": receipt.evidence,
            },
            ack_verified=False,
        )

    def inspect_status(self, endpoint: AgentEndpoint) -> dict[str, Any]:
        endpoint = self.bind(endpoint)
        return self.control.inspect(endpoint.external_id)

    def verify_identity(self, endpoint: AgentEndpoint, evidence: dict[str, Any]) -> None:
        observed = evidence.get("thread_id") or evidence.get("task_id")
        if observed != endpoint.external_id:
            raise IntegrityError("Codex thread/task evidence does not match endpoint")

    def verify_model(self, request: ModelRequest, evidence: ModelEvidence) -> None:
        ModelPolicy.validate_request(request)
        ModelPolicy.validate_evidence(evidence)
        if request.provider_model_id and evidence.provider_model_id and request.provider_model_id != evidence.provider_model_id:
            raise IntegrityError("provider model evidence does not match request")

    def capture_backend_evidence(self, endpoint: AgentEndpoint) -> dict[str, Any]:
        return {"backend_type": "codex_thread", "thread_id": endpoint.external_id, "status": self.inspect_status(endpoint)}
