"""Deterministic ownership transfer around a durable mailbox message."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import IntegrityError
from .mailbox import MailboxStore
from .model_policy import ModelEvidence
from .process import AgentLauncher, LaunchAuthority, LaunchSpec, ProcessHandle, StartupAck, verify_startup_ack
from .runtime import RunPaths, RuntimeLayout
from .schemas import BootstrapCapsule, MailboxMessage, OwnershipRecord, utc_now
from .storage import read_json


@dataclass(frozen=True)
class HandoffPlan:
    run_id: str
    message_id: str
    current_role: str
    next_role: str
    turn_id: str
    launch_spec: LaunchSpec
    ownership_ref: Path


class HandoffCoordinator:
    """Prepare first, then commit only after successor PID/ACK/model evidence agree."""

    def __init__(self, layout: RuntimeLayout, launcher: AgentLauncher):
        self.layout = layout
        self.launcher = launcher
        self.authority = LaunchAuthority(layout.profile)

    def prepare(
        self,
        paths: RunPaths,
        message: MailboxMessage,
        capsule: BootstrapCapsule,
        current_role: str,
        current_pid: int | None,
        semantic_routing: dict[str, object] | None = None,
    ) -> HandoffPlan:
        message.validate()
        capsule.validate()
        if message.recipient_role != capsule.next_role:
            raise IntegrityError("message recipient and capsule next_role disagree")
        if message.run_id != capsule.run_id or message.turn_id != capsule.turn_id:
            raise IntegrityError("message and capsule identity disagree")
        spec = self.authority.build_spec(capsule, paths.root / message.capsule_ref, semantic_routing)
        record = OwnershipRecord(
            run_id=message.run_id,
            turn_id=message.turn_id,
            current_role=current_role,
            message_id=message.message_id,
            owner_pid=current_pid,
            state="handoff_pending",
            successor_role=message.recipient_role,
            predecessor_role=current_role,
        )
        owner_ref = self.layout.write_ownership(paths, record)
        return HandoffPlan(message.run_id, message.message_id, current_role, message.recipient_role, message.turn_id, spec, owner_ref)

    def commit_after_ack(
        self,
        paths: RunPaths,
        plan: HandoffPlan,
        capsule: BootstrapCapsule,
        handle: ProcessHandle,
        ack: StartupAck,
        model_evidence: ModelEvidence,
    ) -> Path:
        if ack.role != plan.next_role or Path(ack.capsule_path).name != plan.launch_spec.capsule_path.name:
            raise IntegrityError("successor ACK role or capsule mismatch")
        verify_startup_ack(ack, handle)
        self.launcher.verify_model(model_evidence)
        mailbox = self.layout.mailbox(paths)
        mailbox.claim(plan.message_id, plan.next_role, handle.pid, plan.turn_id)
        record = OwnershipRecord(
            run_id=plan.run_id,
            turn_id=plan.turn_id,
            current_role=plan.next_role,
            message_id=plan.message_id,
            owner_pid=handle.pid,
            state="owned",
            successor_role=None,
            predecessor_role=plan.current_role,
            updated_at=utc_now(),
        )
        return self.layout.write_ownership(paths, record)

    def recover_next_action(self, paths: RunPaths) -> str:
        records = sorted(paths.owners.glob("*.json"))
        if not records:
            return "no_owner_record"
        record = OwnershipRecord.from_dict(read_json(records[-1]))
        if record.state == "handoff_pending":
            return f"launch_successor:{record.successor_role}:{record.message_id}"
        if record.state == "owned":
            return f"continue_owner:{record.current_role}:{record.message_id}"
        return "no_action"
