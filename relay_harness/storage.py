"""Crash-safe, content-addressable file primitives."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import IntegrityError


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> str:
    """Write bytes durably, replacing the destination only after fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return sha256_bytes(data)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_create_bytes(path: Path, data: bytes) -> str:
    """Create a file exactly once; callers use this for immutable records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return sha256_bytes(data)
    except Exception:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> str:
    return atomic_write_bytes(path, canonical_json(value))


def atomic_create_json(path: Path, value: Any) -> str:
    return atomic_create_bytes(path, canonical_json(value))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def verify_hash(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise IntegrityError(f"hash mismatch for {path}: expected {expected}, got {actual}")
