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
from .schemas import (
    BootstrapCapsule,
    Claim,
    MailboxMessage,
    OwnershipRecord,
    ResultCapsule,
    TaskCapsule,
    TerminalContract,
    TerminalDecision,
    utc_now,
)
from .storage import atomic_create_json, atomic_write_json, read_json, sha256_file


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

    @staticmethod
    def _run_file(paths: RunPaths, reference: str) -> Path:
        """Resolve a durable reference without permitting path escape."""
        candidate = Path(reference)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise IntegrityError(f"run reference escapes runtime root: {reference}")
        target = paths.root / candidate
        try:
            target.relative_to(paths.root)
        except ValueError as exc:
            raise IntegrityError(f"run reference escapes runtime root: {reference}") from exc
        return target

    @staticmethod
    def terminal_decision_path(paths: RunPaths, capsule: BootstrapCapsule, activation_id: str) -> Path:
        contract = TerminalContract.from_dict(capsule.terminal_contract)
        reference = contract.decision_path_template.format(turn_id=capsule.turn_id, activation_id=activation_id)
        return RuntimeLayout._run_file(paths, reference)

    def write_terminal_decision(self, paths: RunPaths, capsule: BootstrapCapsule, decision: TerminalDecision) -> Path:
        """Persist semantic output at the Kernel-provided path; never update the manifest."""
        capsule.validate()
        decision.validate()
        target = self.terminal_decision_path(paths, capsule, decision.activation_id)
        if target.exists():
            existing = TerminalDecision.from_dict(read_json(target))
            if existing.to_dict() != decision.to_dict():
                raise IntegrityError("terminal decision path already contains a different decision")
            return target
        atomic_create_json(target, decision.to_dict())
        return target

    def _activation_for(self, activation_id: str):
        path = self.endpoint_registry().activations / f"{activation_id}.json"
        if not path.exists():
            raise IntegrityError(f"terminal decision references unknown activation: {activation_id}")
        from .endpoints import ActivationRecord
        return ActivationRecord.from_dict(read_json(path))

    def validate_terminal_decision(
        self,
        paths: RunPaths,
        capsule: BootstrapCapsule,
        decision: TerminalDecision,
        endpoint=None,
        identity=None,
    ) -> dict[str, Any]:
        """Validate semantic output and all referenced files before terminal commit."""
        capsule.validate()
        decision.validate()
        contract = TerminalContract.from_dict(capsule.terminal_contract)
        manifest = read_json(paths.manifest)
        if manifest.get("project_id") != capsule.project_id or manifest.get("run_id") != capsule.run_id:
            raise IntegrityError("terminal decision disagrees with run manifest")
        if manifest.get("current_turn_id") not in {None, capsule.turn_id}:
            raise IntegrityError("terminal decision is not for the current terminal turn")
        if decision.project_id != capsule.project_id or decision.run_id != capsule.run_id or decision.turn_id != capsule.turn_id:
            raise IntegrityError("terminal decision identity disagrees with capsule")
        if decision.role != capsule.role or capsule.role != contract.authority_role or capsule.successor_role is not None:
            raise IntegrityError("terminal capsule is not a Coordinator terminal authority with no successor")
        if decision.decision not in contract.allowed_decisions:
            raise IntegrityError("terminal decision is not allowed by the capsule contract")
        expected_path = self.terminal_decision_path(paths, capsule, decision.activation_id)
        if not expected_path.is_file():
            raise IntegrityError("durable terminal decision is missing")
        persisted = TerminalDecision.from_dict(read_json(expected_path))
        if persisted.to_dict() != decision.to_dict():
            raise IntegrityError("in-memory terminal decision disagrees with durable decision")
        if endpoint is not None and (endpoint.endpoint_id != decision.endpoint_id or endpoint.role != capsule.role):
            raise IntegrityError("terminal decision endpoint identity mismatch")
        if identity is not None:
            if identity.activation_id != decision.activation_id or identity.endpoint_id != decision.endpoint_id or identity.role != capsule.role:
                raise IntegrityError("terminal decision activation identity mismatch")
        activation = self._activation_for(decision.activation_id)
        if activation.identity.endpoint_id != decision.endpoint_id or activation.identity.role != capsule.role:
            raise IntegrityError("terminal decision activation record mismatch")
        if endpoint is None:
            registered = self.endpoint_registry().get(capsule.role)
            if registered.endpoint_id != decision.endpoint_id:
                raise IntegrityError("terminal decision does not target the bound endpoint")
        summary_path = self._run_file(paths, decision.summary_ref)
        if contract.summary_required and not summary_path.is_file():
            raise IntegrityError("terminal decision summary reference is missing")
        required_results = decision.decision in contract.result_refs_required_for
        if required_results and not decision.result_refs:
            raise IntegrityError("terminal decision requires at least one result reference")
        if decision.decision in contract.concern_refs_required_for and not decision.concern_refs:
            raise IntegrityError("terminal decision requires at least one concern reference")
        refs: dict[str, str] = {decision.summary_ref: sha256_file(summary_path)}
        for reference in [*decision.result_refs, *decision.concern_refs]:
            path = self._run_file(paths, reference)
            if not path.is_file():
                raise IntegrityError(f"terminal decision reference is missing: {reference}")
            refs[reference] = sha256_file(path)
        for role in ("relay", "coordinator", "worker"):
            for state in ("pending", "claimed", "done"):
                for message_path in sorted((paths.mailbox / role / state).glob("*.json")):
                    message = MailboxMessage.from_dict(read_json(message_path))
                    if message.sender_role == capsule.role and message.turn_id == capsule.turn_id:
                        raise IntegrityError("terminal activation also created a conflicting continuation")
        return {"decision_path": str(expected_path), "references": refs, "activation_id": decision.activation_id}

    def commit_terminal_decision(
        self,
        paths: RunPaths,
        capsule: BootstrapCapsule,
        decision: TerminalDecision,
        endpoint=None,
        identity=None,
    ) -> Path:
        """Commit a validated semantic decision as the authoritative terminal state."""
        validation = self.validate_terminal_decision(paths, capsule, decision, endpoint, identity)
        target = paths.terminal / "authoritative.json"
        terminal_status = {
            "COMPLETE": "completed",
            "HUMAN_REQUIRED": "waiting_for_external_audit",
            "FAILED": "failed",
        }[decision.decision]
        record = {
            "kind": "authoritative_terminal",
            "status": terminal_status,
            "decision": decision.decision,
            "terminal_decision_ref": validation["decision_path"],
            "terminal_decision_sha256": sha256_file(Path(validation["decision_path"])),
            "summary_ref": decision.summary_ref,
            "result_refs": list(decision.result_refs),
            "concern_refs": list(decision.concern_refs),
            "reference_sha256": validation["references"],
            "project_id": decision.project_id,
            "run_id": decision.run_id,
            "turn_id": decision.turn_id,
            "activation_id": decision.activation_id,
            "endpoint_id": decision.endpoint_id,
            "role": decision.role,
            "recorded_at": utc_now(),
        }
        if target.exists():
            existing = read_json(target)
            if existing.get("terminal_decision_sha256") != record["terminal_decision_sha256"]:
                raise IntegrityError("authoritative terminal record conflicts with durable decision")
        else:
            atomic_create_json(target, record)
        manifest = read_json(paths.manifest)
        if manifest.get("status") != terminal_status:
            manifest["status"] = terminal_status
            manifest["terminal_ref"] = str(target)
            manifest["terminal_decision_ref"] = validation["decision_path"]
            manifest["current_turn_id"] = decision.turn_id
            atomic_write_json(paths.manifest, manifest)
        elif manifest.get("terminal_ref") != str(target):
            raise IntegrityError("completed manifest points to a different terminal record")
        return target

    def inspect_recovery(self, run_id: str) -> dict[str, Any]:
        """Report structural recovery facts without making semantic decisions or launching agents."""
        paths = self.run(run_id)
        manifest = read_json(paths.manifest)
        capsule_files = sorted(paths.capsules.glob("*.json"))
        capsules: list[dict[str, Any]] = []
        capsule_objects: list[BootstrapCapsule] = []
        legacy_capsules: list[dict[str, Any]] = []
        errors: list[str] = []
        for path in capsule_files:
            try:
                raw = read_json(path)
                if raw.get("protocol_version") == "1" and "next_role" in raw:
                    legacy_capsules.append({"path": str(path), "protocol_version": "1", "role": raw.get("role"), "turn_id": raw.get("turn_id"), "legacy_next_role": raw.get("next_role"), "resumable": False})
                    continue
                capsule = BootstrapCapsule.from_dict(raw)
                capsule_objects.append(capsule)
                capsules.append({"path": str(path), "protocol_version": capsule.protocol_version, "role": capsule.role, "turn_id": capsule.turn_id, "successor_role": capsule.successor_role, "resumable": True})
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
        terminal_statuses = {"completed", "failed", "stopped", "waiting_for_external_audit"}
        next_action = "no_owner_record"
        if manifest.get("status") in terminal_statuses:
            next_action = "no_action"
        elif legacy_capsules:
            next_action = "legacy_protocol_v1_requires_manual_migration"
        if ownership_records:
            latest = ownership_records[-1]
            if manifest.get("status") in terminal_statuses:
                next_action = "no_action"
            elif legacy_capsules:
                next_action = "legacy_protocol_v1_requires_manual_migration"
            elif latest["state"] == "handoff_pending":
                next_action = f"launch_successor:{latest['successor_role']}:{latest['message_id']}"
            elif latest["state"] == "owned":
                next_action = f"continue_owner:{latest['current_role']}:{latest['message_id']}"
            else:
                next_action = "no_action"
        terminal_decisions: list[dict[str, Any]] = []
        terminal_commit_pending = False
        for decision_path in sorted((paths.terminal / "decisions").glob("*.json")):
            try:
                decision = TerminalDecision.from_dict(read_json(decision_path))
                capsule = next(item for item in capsule_objects if item.turn_id == decision.turn_id and item.role == decision.role)
                validation = self.validate_terminal_decision(paths, capsule, decision)
                terminal_decisions.append({
                    "path": str(decision_path),
                    "decision": decision.decision,
                    "activation_id": decision.activation_id,
                    "valid": True,
                    "references": validation["references"],
                })
                if manifest.get("status") not in terminal_statuses:
                    terminal_commit_pending = True
            except Exception as exc:
                errors.append(f"{decision_path.name}: {exc}")
        if terminal_commit_pending and manifest.get("status") not in terminal_statuses:
            next_action = "terminal_commit_pending"
        endpoint_records: list[dict[str, Any]] = []
        activation_records: list[dict[str, Any]] = []
        try:
            registry = self.endpoint_registry()
            endpoint_records = [endpoint.to_dict() for endpoint in registry.list()]
            for activation_path in sorted(registry.activations.glob("*.json")):
                activation_records.append(read_json(activation_path))
        except (FileNotFoundError, RecoveryError):
            pass
        complete = bool(capsules) and not legacy_capsules and not errors
        return {
            "project_id": manifest.get("project_id"),
            "run_id": manifest.get("run_id"),
            "status": manifest.get("status"),
            "manifest_present": True,
            "capsules": capsules,
            "legacy_capsules": legacy_capsules,
            "tasks": [str(path) for path in task_files],
            "results": [str(path) for path in result_files],
            "claims": [str(path) for path in sorted(paths.claims.glob("*.json"))],
            "mailbox": {
                role: {
                    state: [str(path) for path in sorted((paths.mailbox / role / state).glob("*.json"))]
                    for state in ("pending", "claimed", "done")
                }
                for role in ("relay", "coordinator", "worker")
            },
            "ownership": [str(path) for path in ownership_files],
            "ownership_records": ownership_records,
            "endpoints": endpoint_records,
            "activations": activation_records,
            "next_deterministic_action": next_action,
            "continuation_structurally_complete": complete,
            "terminal_decisions": terminal_decisions,
            "terminal_commit_pending": terminal_commit_pending,
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
        terminal_statuses = {"completed", "failed", "stopped", "waiting_for_external_audit"}
        is_terminal = bool(latest and latest.get("status") in terminal_statuses)
        return {
            "project": self.profile.project_id,
            "run": latest.get("run_id") if latest else None,
            "mode": self.profile.policy.mode,
            "run_status": latest.get("status") if latest else "not_started",
            "coordinator": endpoint_by_role.get("coordinator"),
            "worker": endpoint_by_role.get("worker"),
            "current_owner": None if is_terminal else ownership,
            "historical_owner": ownership,
            "next_expected_role": None if is_terminal else (ownership.get("successor_role") if ownership else None),
            "current_workflow": "terminal" if is_terminal else "running",
            "next_role": "none" if is_terminal else (ownership.get("successor_role") if ownership else None),
            "health": (
                "unknown" if not latest else
                "awaiting_coordinator" if latest.get("status") == "awaiting_coordinator" else
                latest.get("status") if latest.get("status") in {"completed", "failed", "stopped", "waiting_for_external_audit"} else
                "running"
            ),
        }
