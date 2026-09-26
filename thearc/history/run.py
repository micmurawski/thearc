"""Durable lifecycle journal and checkpoint helpers for adaptation runs."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .contracts import RunCheckpoint


class RunJournalRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    event: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunJournal:
    """Append-only lifecycle journal, separate from resource-change history."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: RunJournalRecord) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(record.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record(self, run_id: str, event: str, *, node: str | None = None, **payload: Any) -> RunJournalRecord:
        entry = RunJournalRecord(run_id=run_id, event=event, node=node, payload=payload)
        self.append(entry)
        return entry

    def records(self, run_id: str | None = None) -> list[RunJournalRecord]:
        if not self.path.exists():
            return []
        records = [RunJournalRecord.model_validate_json(line) for line in self.path.read_text().splitlines() if line]
        return [record for record in records if run_id is None or record.run_id == run_id]


class CheckpointStore:
    """Atomically persisted latest checkpoint for one flow run."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: RunCheckpoint) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(checkpoint.model_dump(mode="json"), stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> RunCheckpoint | None:
        if not self.path.exists():
            return None
        return RunCheckpoint.model_validate_json(self.path.read_text(encoding="utf-8"))


class AdaptationRun:
    """Context manager for consistent start, completion, failure, and state writes."""

    def __init__(
        self,
        run_id: str,
        *,
        flow_name: str,
        flow_version: str,
        journal: RunJournal | None = None,
        checkpoint: CheckpointStore | None = None,
    ):
        self.run_id = run_id
        self.flow_name = flow_name
        self.flow_version = flow_version
        self.journal = journal
        self.checkpoint = checkpoint
        self._completed = False

    def __enter__(self) -> AdaptationRun:  # noqa: PYI034 -- Python 3.10 has no typing.Self
        if self.journal:
            self.journal.record(self.run_id, "adaptation_started", node="query")
        self.save("running", "query", {"status": "started"})
        return self

    def save(
        self,
        status: str,
        node: str,
        state: dict[str, Any],
        *,
        completed_nodes: list[str] | None = None,
    ) -> None:
        if self.checkpoint:
            self.checkpoint.save(
                RunCheckpoint(
                    run_id=self.run_id,
                    flow_name=self.flow_name,
                    flow_version=self.flow_version,
                    status=status,
                    node=node,
                    completed_nodes=completed_nodes or [],
                    state=state,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    def complete(self, state: dict[str, Any], *, node: str = "update") -> None:
        self._completed = True
        if self.journal:
            self.journal.record(self.run_id, "adaptation_completed", node=node, **state)
        self.save(
            "completed",
            node,
            state,
            completed_nodes=["query", "materialize", "reflection", "curation", "update"],
        )

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is not None and not self._completed:
            error = exc_type.__name__ if exc_type else "unknown"
            if self.journal:
                self.journal.record(self.run_id, "adaptation_failed", node="flow", error=error)
            self.save("failed", "flow", {"error": error})
        elif not self._completed:
            if self.journal:
                self.journal.record(self.run_id, "adaptation_abandoned", node="flow")
            self.save("failed", "flow", {"error": "run exited without completion"})
        return False
