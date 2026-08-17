"""Runtime directory layout, durable run creation, claims, and recovery inspection."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import ProjectProfile
from .errors import IntegrityError, RecoveryError
from .endpoints import EndpointRegistry
from .mailbox import MailboxPaths, MailboxStore
from .schemas import BootstrapCapsule, Claim, OwnershipRecord, ResultCapsule, TaskCapsule, utc_now
from .storage import atomic_write_json, read_json, sha256_file


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def manifest(self) -> Path: return self.root / "run.json"
    @property
    def capsules(self) -> Path: return self.root / "capsules"
    @property
    def tasks(self) -> Path: return self.root / "tasks"
    @property
    def results(self) -> Path: return self.root / "results"
    @property
    def claims(self) -> Path: return self.root / "claims"
    @property
    def owners(self) -> Path: return self.root / "owners"
    @property
    def logs(self) -> Path: return self.root / "logs"
    @property
    def incidents(self) -> Path: return self.root / "incidents"
    @property
    def terminal(self) -> Path: return self.root / "terminal"

    @property
    def mailbox(self) -> Path: return self.root / "mailbox"

    @property
    def messages(self) -> Path: return self.root / "messages"

    @property
    def payloads(self) -> Path: return self.root / "payloads"

    def create(self) -> None:
        for path in (self.capsules, self.tasks, self.results, self.claims, self.owners, self.logs, self.incidents, self.terminal, self.messages, self.payloads):
            path.mkdir(parents=True, exist_ok=True)
        MailboxPaths(self.root).create()


class RuntimeLayout:
    def __init__(self, profile: ProjectProfile, root_override: str | None = None):
        profile.validate()
        self.profile = profile
        self.root = Path(root_override or profile.runtime.root)
        self.project_state = self.root / "state" / profile.project_id
        self.runs_root = self.root / "runs"

    def ensure(self) -> None:
        for path in (self.project_state, self.runs_root, self.root / "profiles"):
            path.mkdir(parents=True, exist_ok=True)
        objective = self.project_state / "objective.md"
        if not objective.exists():
            objective.write_text("# Durable project objective\n\nPopulate this file before starting semantic work.\n", encoding="utf-8")
        for name, initial in (("constraints.json", {}), ("decisions.jsonl", ""), ("unresolved.json", {})):
            path = self.project_state / name
            if not path.exists():
                if path.suffix == ".json":
                    atomic_write_json(path, initial)
                else:
                    path.write_text(initial, encoding="utf-8")

    def new_run(self, objective_ref: str | None = None) -> RunPaths:
        self.ensure()
        run_id = f"run_{uuid4().hex}"
        paths = RunPaths(self.runs_root / run_id)
        paths.create()
        manifest = {
            "schema_version": "1",
            "project_id": self.profile.project_id,
            "run_id": run_id,
            "created_at": utc_now(),
            "status": "created",
            "current_turn_id": None,
            "objective_ref": objective_ref or str(self.project_state / "objective.md"),
            "profile_ref": str(self.root / "profiles" / f"{self.profile.project_id}.json"),
        }
        atomic_write_json(paths.manifest, manifest)
        return paths

    def run(self, run_id: str) -> RunPaths:
        paths = RunPaths(self.runs_root / run_id)
        if not paths.manifest.exists():
            raise RecoveryError(f"unknown run: {run_id}")
        return paths

    def write_bootstrap(self, paths: RunPaths, capsule: BootstrapCapsule) -> Path:
        capsule.validate()
        target = paths.capsules / f"{capsule.turn_id}_{capsule.role}.json"
        atomic_write_json(target, capsule.to_dict())
        return target

    def write_task(self, paths: RunPaths, task: TaskCapsule) -> Path:
        task.validate()
        target = paths.tasks / f"{task.task_id}.json"
        atomic_write_json(target, task.to_dict())
        return target

    def write_result(self, paths: RunPaths, result: ResultCapsule) -> Path:
        result.validate()
        target = paths.results / f"{result.result_id}.json"
        atomic_write_json(target, result.to_dict())
        return target

    def mailbox(self, paths: RunPaths) -> MailboxStore:
        return MailboxStore(MailboxPaths(paths.root))

    def endpoint_registry(self) -> EndpointRegistry:
        return EndpointRegistry(self.root)

    def write_ownership(self, paths: RunPaths, record: OwnershipRecord) -> Path:
        record.validate()
        target = paths.owners / f"{record.turn_id}.json"
        atomic_write_json(target, record.to_dict())
        return target

    def acquire_claim(self, paths: RunPaths, claim: Claim) -> Path:
        """Create-if-absent claim file; two owners cannot silently overwrite each other."""
        claim.validate()
        target = paths.claims / f"{claim.resource_type}_{claim.resource_id}.json"
        if target.exists():
            existing = Claim.from_dict(read_json(target))
            if existing.owner_role != claim.owner_role or existing.released_at is None:
                raise IntegrityError(f"resource already claimed: {claim.resource_type}/{claim.resource_id}")
        atomic_write_json(target, claim.to_dict())
        return target

    def inspect_recovery(self, run_id: str) -> dict[str, Any]:
        """Report structural recovery facts without making semantic decisions or launching agents."""
        paths = self.run(run_id)
        manifest = read_json(paths.manifest)
        capsule_files = sorted(paths.capsules.glob("*.json"))
        capsules: list[dict[str, Any]] = []
        errors: list[str] = []
        for path in capsule_files:
            try:
                capsule = BootstrapCapsule.from_dict(read_json(path))
                capsules.append({"path": str(path), "role": capsule.role, "turn_id": capsule.turn_id, "next_role": capsule.next_role})
            except Exception as exc:  # inspection must preserve evidence rather than abort
                errors.append(f"{path.name}: {exc}")
        task_files = sorted(paths.tasks.glob("*.json"))
        result_files = sorted(paths.results.glob("*.json"))
        ownership_files = sorted(paths.owners.glob("*.json"))
        ownership_records: list[dict[str, Any]] = []
        for path in ownership_files:
            try:
                ownership_records.append(OwnershipRecord.from_dict(read_json(path)).to_dict())
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
        next_action = "no_owner_record"
        if ownership_records:
            latest = ownership_records[-1]
            if latest["state"] == "handoff_pending":
                next_action = f"launch_successor:{latest['successor_role']}:{latest['message_id']}"
            elif latest["state"] == "owned":
                next_action = f"continue_owner:{latest['current_role']}:{latest['message_id']}"
            else:
                next_action = "no_action"
        endpoint_records: list[dict[str, Any]] = []
        activation_records: list[dict[str, Any]] = []
        try:
            registry = self.endpoint_registry()
            endpoint_records = [endpoint.to_dict() for endpoint in registry.list()]
            for activation_path in sorted(registry.activations.glob("*.json")):
                activation_records.append(read_json(activation_path))
        except (FileNotFoundError, RecoveryError):
            pass
        complete = bool(capsules) and not errors and all(item.get("next_role") is not None for item in capsules[-1:])
        return {
            "project_id": manifest.get("project_id"),
            "run_id": manifest.get("run_id"),
            "status": manifest.get("status"),
            "manifest_present": True,
            "capsules": capsules,
            "tasks": [str(path) for path in task_files],
            "results": [str(path) for path in result_files],
            "claims": [str(path) for path in sorted(paths.claims.glob("*.json"))],
            "mailbox": {
                role: {
                    state: [str(path) for path in sorted((paths.mailbox / role / state).glob("*.json"))]
                    for state in ("pending", "claimed", "done")
                }
                for role in ("relay", "worker")
            },
            "ownership": [str(path) for path in ownership_files],
            "ownership_records": ownership_records,
            "endpoints": endpoint_records,
            "activations": activation_records,
            "next_deterministic_action": next_action,
            "continuation_structurally_complete": complete,
            "errors": errors,
        }

    def mark_terminal(self, paths: RunPaths, reason: str, status: str = "stopped") -> Path:
        if status not in {"stopped", "completed", "failed", "waiting_for_external_audit"}:
            raise ValueError(f"invalid terminal status: {status}")
        record = {"status": status, "reason": reason, "recorded_at": utc_now()}
        target = paths.terminal / f"{utc_now().replace(':', '').replace('+', '_')}.json"
        atomic_write_json(target, record)
        manifest = read_json(paths.manifest)
        manifest["status"] = status
        manifest["terminal_ref"] = str(target)
        atomic_write_json(paths.manifest, manifest)
        return target

    def status_summary(self) -> dict[str, Any]:
        manifests = sorted(self.runs_root.glob("*/run.json"))
        latest = read_json(manifests[-1]) if manifests else None
        endpoints = []
        try:
            endpoints = [endpoint.to_dict() for endpoint in self.endpoint_registry().list()]
        except (FileNotFoundError, RecoveryError):
            endpoints = []
        ownership: dict[str, Any] | None = None
        if latest:
            paths = RunPaths(self.runs_root / latest["run_id"])
            owner_files = sorted(paths.owners.glob("*.json"))
            if owner_files:
                ownership = OwnershipRecord.from_dict(read_json(owner_files[-1])).to_dict()
        endpoint_by_role = {item["role"]: item for item in endpoints}
        return {
            "project": self.profile.project_id,
            "run": latest.get("run_id") if latest else None,
            "mode": self.profile.policy.mode,
            "run_status": latest.get("status") if latest else "not_started",
            "coordinator": endpoint_by_role.get("coordinator"),
            "worker": endpoint_by_role.get("worker"),
            "current_owner": ownership,
            "next_expected_role": ownership.get("successor_role") if ownership else None,
            "health": "awaiting_coordinator" if latest and latest.get("status") == "awaiting_coordinator" else "unknown" if not latest else "running",
        }
