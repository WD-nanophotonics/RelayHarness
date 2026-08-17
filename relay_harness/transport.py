"""Supervisory transport boundary; the kernel does not know ChatGPT, Chrome, or Gmail."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .storage import atomic_create_json, atomic_write_bytes, read_json, sha256_bytes
from .schemas import utc_now


@dataclass(frozen=True)
class ExternalMessage:
    external_id: str | None
    body_ref: str
    received_at: str


class SupervisoryTransport(ABC):
    @abstractmethod
    def read_latest(self) -> ExternalMessage | None:
        raise NotImplementedError

    @abstractmethod
    def submit(self, payload_ref: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify(self, submission_id: str) -> bool:
        raise NotImplementedError


class ManualTransport(SupervisoryTransport):
    """Explicit no-network adapter used by the foundation and local operators."""

    def read_latest(self) -> ExternalMessage | None:
        return None

    def submit(self, payload_ref: str) -> str:
        raise RuntimeError("manual transport cannot submit externally")

    def verify(self, submission_id: str) -> bool:
        return False


class BrowserChatBridge(Protocol):
    """Minimal browser-side capability injected by an external adapter host."""

    def read_latest(self) -> tuple[str | None, str] | None: ...

    def submit(self, body: str) -> str: ...

    def verify(self, submission_id: str) -> bool: ...


class ChromeDOMTransport(SupervisoryTransport):
    """Optional adapter: browser mechanics stay behind the injected bridge.

    The bridge may be backed by Chrome DOM control, but the Kernel sees only this
    file-backed transport contract. Inbound and outbound content is durable before
    the caller receives a success result.
    """

    def __init__(self, root: Path, bridge: BrowserChatBridge):
        self.root = root
        self.bridge = bridge
        self.inbound = root / "external" / "inbound"
        self.outbound = root / "external" / "outbound"
        self.verifications = root / "external" / "verifications"
        for path in (self.inbound, self.outbound, self.verifications):
            path.mkdir(parents=True, exist_ok=True)

    def read_latest(self) -> ExternalMessage | None:
        received = self.bridge.read_latest()
        if received is None:
            return None
        external_id, body = received
        digest = sha256_bytes(body.encode("utf-8"))
        body_path = self.inbound / f"{digest}.md"
        if not body_path.exists():
            atomic_write_bytes(body_path, body.encode("utf-8"))
        return ExternalMessage(external_id, str(body_path), utc_now())

    def submit(self, payload_ref: str) -> str:
        payload_path = Path(payload_ref)
        body = payload_path.read_text(encoding="utf-8")
        submission_id = self.bridge.submit(body)
        atomic_create_json(
            self.outbound / f"{submission_id}.json",
            {"submission_id": submission_id, "payload_ref": payload_ref, "submitted_at": utc_now()},
        )
        return submission_id

    def verify(self, submission_id: str) -> bool:
        verified = self.bridge.verify(submission_id)
        atomic_create_json(
            self.verifications / f"{submission_id}.json",
            {"submission_id": submission_id, "verified": verified, "verified_at": utc_now()},
        )
        return verified
