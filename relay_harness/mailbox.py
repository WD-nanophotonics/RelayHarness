"""A small durable mailbox for semantic A/B handoffs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import IntegrityError, SchemaError
from .schemas import Claim, MailboxMessage, utc_now
from .storage import atomic_create_bytes, atomic_create_json, atomic_write_json, read_json, sha256_bytes, sha256_file


@dataclass(frozen=True)
class MailboxPaths:
    run_root: Path

    @property
    def mailbox(self) -> Path:
        return self.run_root / "mailbox"

    @property
    def payloads(self) -> Path:
        return self.run_root / "payloads"

    @property
    def messages(self) -> Path:
        return self.run_root / "messages"

    @property
    def claims(self) -> Path:
        return self.run_root / "claims"

    def create(self) -> None:
        for role in ("relay", "worker"):
            for state in ("pending", "claimed", "done"):
                (self.mailbox / role / state).mkdir(parents=True, exist_ok=True)
        for path in (self.payloads, self.messages, self.claims):
            path.mkdir(parents=True, exist_ok=True)

    def state_path(self, recipient_role: str, state: str, message_id: str) -> Path:
        if recipient_role not in {"relay", "worker"} or state not in {"pending", "claimed", "done"}:
            raise ValueError("invalid mailbox role or state")
        return self.mailbox / recipient_role / state / f"{message_id}.json"


class MailboxStore:
    """Kernel-side publication and ownership operations for immutable messages."""

    def __init__(self, paths: MailboxPaths):
        self.paths = paths
        self.paths.create()

    def publish_payload(self, name: str, content: bytes) -> tuple[str, str]:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("payload name must be relative and path-safe")
        target = self.paths.payloads / relative
        digest = sha256_bytes(content)
        try:
            atomic_create_bytes(target, content)
        except FileExistsError:
            if sha256_file(target) != digest:
                raise IntegrityError(f"immutable payload conflict: {target}")
        return str(Path("payloads") / relative).replace("\\", "/"), digest

    def publish_message(self, message: MailboxMessage) -> Path:
        message.seal()
        message.validate()
        payload = self.paths.run_root / message.payload_ref
        capsule = self.paths.run_root / message.capsule_ref
        if not payload.is_file():
            raise SchemaError(f"mailbox payload does not exist: {message.payload_ref}")
        if not capsule.is_file():
            raise SchemaError(f"mailbox capsule does not exist: {message.capsule_ref}")
        if sha256_file(payload) != message.payload_sha256:
            raise IntegrityError(f"mailbox payload hash mismatch: {message.message_id}")
        target = self.paths.state_path(message.recipient_role, "pending", message.message_id)
        try:
            atomic_create_json(target, message.to_dict())
        except FileExistsError:
            existing = MailboxMessage.from_dict(read_json(target))
            if existing.message_sha256 != message.message_sha256:
                raise IntegrityError(f"immutable mailbox message conflict: {message.message_id}")
        return target

    def load(self, message_id: str, recipient_role: str) -> tuple[MailboxMessage, str, Path]:
        for state in ("pending", "claimed", "done"):
            path = self.paths.state_path(recipient_role, state, message_id)
            if path.exists():
                message = MailboxMessage.from_dict(read_json(path))
                if message.recipient_role != recipient_role:
                    raise IntegrityError("message recipient does not match mailbox")
                payload = self.paths.run_root / message.payload_ref
                if not payload.is_file() or sha256_file(payload) != message.payload_sha256:
                    raise IntegrityError(f"message payload is missing or corrupt: {message_id}")
                return message, state, path
        raise FileNotFoundError(message_id)

    def claim(
        self,
        message_id: str,
        recipient_role: str,
        owner_pid: int | None,
        turn_id: str,
        owner_endpoint_id: str | None = None,
        owner_activation_id: str | None = None,
        owner_backend_type: str | None = None,
    ) -> Path:
        message, state, source = self.load(message_id, recipient_role)
        if state == "done":
            raise IntegrityError(f"message already completed: {message_id}")
        claim_path = self.paths.claims / f"message_{message_id}.json"
        claim = Claim("message", message_id, recipient_role, owner_pid, turn_id, owner_endpoint_id, owner_activation_id, owner_backend_type)
        try:
            atomic_create_json(claim_path, claim.to_dict())
        except FileExistsError:
            existing = Claim.from_dict(read_json(claim_path))
            if existing.owner_role != recipient_role or existing.owner_pid != owner_pid or existing.released_at is not None:
                raise IntegrityError(f"duplicate mailbox claim: {message_id}")
        if state == "pending":
            target = self.paths.state_path(recipient_role, "claimed", message_id)
            try:
                os.replace(source, target)
            except FileNotFoundError:
                if not target.exists():
                    raise
            return target
        return source

    def complete(self, message_id: str, recipient_role: str, owner_pid: int | None) -> Path:
        message, state, source = self.load(message_id, recipient_role)
        if state != "claimed":
            raise IntegrityError(f"message is not claimed: {message_id}")
        claim_path = self.paths.claims / f"message_{message_id}.json"
        claim = Claim.from_dict(read_json(claim_path))
        if claim.owner_role != recipient_role or claim.owner_pid != owner_pid or claim.released_at is not None:
            raise IntegrityError(f"claim owner mismatch: {message_id}")
        claim.released_at = utc_now()
        atomic_write_json(claim_path, claim.to_dict())
        target = self.paths.state_path(recipient_role, "done", message_id)
        os.replace(source, target)
        return target

    def state(self, message_id: str, recipient_role: str) -> str:
        return self.load(message_id, recipient_role)[1]

    def pending(self, recipient_role: str) -> list[MailboxMessage]:
        directory = self.mailbox_dir(recipient_role, "pending")
        return [MailboxMessage.from_dict(read_json(path)) for path in sorted(directory.glob("*.json"))]

    def mailbox_dir(self, recipient_role: str, state: str) -> Path:
        return self.paths.mailbox / recipient_role / state
