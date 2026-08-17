"""Cheap normal journal and richer incident evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage import atomic_write_json
from .schemas import utc_now


class StructuredJournal:
    def __init__(self, path: Path, identity: dict[str, Any] | None = None):
        self.path = path
        self.identity = identity or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **facts: Any) -> None:
        record = {"timestamp": utc_now(), "event": event, **self.identity, **facts}
        with self.path.open("a", encoding="utf-8") as stream:
            import json
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()


class IncidentRecorder:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def record(self, incident_id: str, concern: dict[str, Any], objective_evidence: list[dict[str, Any]]) -> Path:
        bundle = self.root / incident_id
        bundle.mkdir(parents=True, exist_ok=False)
        atomic_write_json(bundle / "incident.json", {"incident_id": incident_id, "created_at": utc_now(), "concern": concern})
        atomic_write_json(bundle / "objective_evidence.json", {"records": objective_evidence})
        return bundle
