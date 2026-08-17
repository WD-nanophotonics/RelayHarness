"""Supervisory transport boundary; the kernel does not know ChatGPT, Chrome, or Gmail."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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
