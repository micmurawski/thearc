"""Immutable epoch records with a locked, atomic HEAD publication."""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from thearc.learning.evidence.snapshot import content_hash, write_json_exclusive
from thearc.models import MetaAgent


def safe_directory(path: str | Path) -> Path:
    path = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Epoch paths must not contain symlinks")
    return path


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
        raise ValueError("Invalid epoch identifier")
    return value


def load_epoch(root: str | Path, epoch_id: str | None = None) -> dict | None:
    root = safe_directory(root)
    head = None
    if epoch_id is None:
        path = safe_directory(root / "HEAD.json")
        if not path.exists():
            return None
        head = json.loads(path.read_text())
        epoch_id = head["epoch_id"]
    path = safe_directory(root / "epochs" / f"{_identifier(epoch_id)}.json")
    record = json.loads(path.read_text())
    data = record["epoch"]
    if (record["sha256"] != content_hash(data) or data["epoch_id"] != epoch_id
            or (head is not None and head["sha256"] != record["sha256"])):
        raise ValueError("Epoch content hash mismatch")
    MetaAgent.model_validate(data["agent"])
    return data


def epoch_history(root: str | Path) -> list[dict]:
    """Follow committed ancestry only; ignore files left by interrupted commits."""
    result, seen = [], set()
    record = load_epoch(root)
    while record is not None:
        if record["epoch_id"] in seen:
            raise ValueError("Cyclic epoch history")
        seen.add(record["epoch_id"])
        result.append(record)
        parent = record["parent"]
        record = load_epoch(root, parent) if parent else None
    return list(reversed(result))


@contextmanager
def _writer(root: Path):
    lock = safe_directory(root / ".commit-lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("Another writer holds the epoch commit lock") from exc
    try:
        yield
    finally:
        lock.rmdir()


def commit_epoch(
    root: str | Path, *, epoch_id: str, expected_head: str | None,
    baseline: MetaAgent, agent: MetaAgent, ledger: dict, reflections: list[dict],
    changes: list[dict], actor: dict, summary: str,
) -> dict:
    """Commit configuration and provenance together, with no approval step.

    The complete immutable record precedes HEAD. A crash before HEAD publication
    leaves the previous committed state intact. A killed writer can leave a lock
    directory; recovery is explicit, never automatic lock stealing.
    """
    root = safe_directory(root)
    _identifier(epoch_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_directory(root / "epochs").mkdir(exist_ok=True)
    with _writer(root):
        current = load_epoch(root)
        if (current["epoch_id"] if current else None) != expected_head:
            raise ValueError("Stale epoch HEAD; rerun curation against the current agent")
        if current is not None and current["agent"] != baseline.model_dump(mode="json"):
            raise ValueError("Baseline differs from the committed MetaAgent")
        data = {
            "schema_version": 1, "epoch_id": epoch_id, "parent": expected_head,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline": baseline.model_dump(mode="json"), "agent": agent.model_dump(mode="json"),
            "ledger": ledger, "reflections": reflections, "changes": changes,
            "actor": actor, "summary": summary,
        }
        digest = content_hash(data)
        write_json_exclusive(root / "epochs" / f"{epoch_id}.json", {"sha256": digest, "epoch": data})
        descriptor, temporary = tempfile.mkstemp(prefix=".head-", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"epoch_id": epoch_id, "sha256": digest}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, safe_directory(root / "HEAD.json"))
            # Make the rename durable on local POSIX filesystems.
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return data
