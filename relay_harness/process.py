"""Process and agent-launch interfaces. No domain agent is implemented here."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .model_policy import ModelEvidence, ModelPolicy, ModelRequest


@dataclass(frozen=True)
class LaunchSpec:
    command: Sequence[str] | str
    cwd: Path
    capsule_path: Path
    role: str
    model_request: ModelRequest
    env: dict[str, str] | None = None


@dataclass
class ProcessHandle:
    pid: int
    process: subprocess.Popen[str]
    started_at: str


@dataclass(frozen=True)
class StartupAck:
    pid: int
    role: str
    capsule_path: str
    acknowledged_at: str


@dataclass(frozen=True)
class LivenessEvidence:
    pid: int
    observed_at: str
    alive: bool
    meaningful_activity_ref: str | None = None


def verify_startup_ack(ack: StartupAck, handle: ProcessHandle) -> None:
    """Verify that the acknowledged process is the process the kernel launched."""
    if ack.pid != handle.pid:
        raise RuntimeError(f"startup ACK PID mismatch: expected {handle.pid}, got {ack.pid}")


def capture_exit(handle: ProcessHandle) -> dict[str, int | None]:
    return {"pid": handle.pid, "returncode": handle.process.poll()}


class AgentLauncher:
    def launch(self, spec: LaunchSpec) -> ProcessHandle:
        raise NotImplementedError

    def verify_model(self, evidence: ModelEvidence) -> None:
        ModelPolicy.validate_evidence(evidence)


class SubprocessLauncher(AgentLauncher):
    """Launches a configured process only after explicit Luna High validation."""

    def launch(self, spec: LaunchSpec) -> ProcessHandle:
        ModelPolicy.validate_request(spec.model_request)
        command = shlex.split(spec.command) if isinstance(spec.command, str) else list(spec.command)
        if not command:
            raise ValueError("cannot launch an empty command")
        environment = os.environ.copy()
        environment.update(spec.env or {})
        environment.update({
            "RELAY_HARNESS_CAPSULE": str(spec.capsule_path),
            "RELAY_HARNESS_ROLE": spec.role,
            "RELAY_HARNESS_MODEL": spec.model_request.model,
            "RELAY_HARNESS_REASONING": spec.model_request.reasoning,
        })
        process = subprocess.Popen(command, cwd=spec.cwd, env=environment, text=True, start_new_session=True)
        from datetime import datetime, timezone
        return ProcessHandle(process.pid, process, datetime.now(timezone.utc).isoformat())
