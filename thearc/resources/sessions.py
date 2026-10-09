"""One entry point for collecting sessions from any harness by location.

`Sessions` registers source directories in a shared index, detects the harness
and collector-batch layouts, and resolves sessions by any recorded identity.
Indexing never writes into a source directory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from thearc.learning.sessions.models import SearchFilters, SourceConfig, SyncReport
from thearc.models import MetaAgent

from .history import SessionStore

DEFAULT_INDEX = Path("~/.thearc/learning.sqlite")
_PROBE_FILES = 20


def _first_record(path):
    try:
        with path.open(encoding="utf-8") as stream:
            for line in islice(stream, 5):
                if line.strip():
                    value = json.loads(line)
                    return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
    return None


def detect_harness(root):
    """Infer the harness from a few native files; raise if absent or ambiguous."""
    root = Path(root).expanduser().resolve()
    votes = set()
    for path in islice(sorted(root.rglob("*.jsonl")), _PROBE_FILES):
        if path.name == "transcript.jsonl":
            votes.add("antigravity")
            continue
        if path.name.startswith("rollout-"):
            votes.add("codex")
            continue
        record = _first_record(path)
        if record is None:
            continue
        if isinstance(record.get("info"), dict) and isinstance(record.get("messages"), list):
            votes.add("opencode")
        elif record.get("type") in {"session_meta", "response_item", "event_msg", "turn_context"}:
            votes.add("codex")
        elif "sessionId" in record:
            votes.add("claude")
        elif record.get("type") == "session" or isinstance(record.get("message"), dict):
            votes.add("pi")
    if len(votes) != 1:
        found = ", ".join(sorted(votes)) or "no recognizable transcripts"
        raise ValueError(f"Cannot infer one harness for {root} ({found}); pass harness= explicitly")
    return votes.pop()


def _batch_runs(root):
    """Collector batches store runs/<id>/manifest.json beside the sessions/ transcripts."""
    for path in sorted((root / "runs").glob("*/manifest.json")):
        try:
            yield path.parent, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def _compact(value):
    return value.replace("-", "").lower()


class Sessions:
    """Collect, list and resolve sessions from many harnesses in one index.

    sessions = Sessions.open()
    sessions.add("~/.codex/sessions")
    sessions.add("~/.claude/projects")
    sessions.add("logs/batch")          # collector batch: also exposes agent snapshots
    sessions.sync()
    history = sessions.get("14a6b5fe").trajectories[0]
    """

    def __init__(self, store):
        self.store = store

    @classmethod
    def open(cls, index=DEFAULT_INDEX):
        path = Path(index).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(SessionStore(path))

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def _index(self):
        return self.store.index

    def add(self, path, *, harness=None, source_id=None, format="native", sync=True):
        """Register a transcript directory or collector batch; returns its SourceConfig."""
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        if (root / "runs").is_dir() and (root / "sessions").is_dir():
            harnesses = {manifest.get("harness", "codex") for _, manifest in _batch_runs(root)}
            if len(harnesses) > 1:
                raise ValueError(f"Collector batch mixes harnesses {sorted(harnesses)}; add its runs separately")
            if harness is None and harnesses:
                harness = harnesses.pop()
            root = root / "sessions"
        if harness is None:
            harness = "claude" if format == "dataclaw" else detect_harness(root)
        if root in self.store.path.parents:
            raise ValueError("Keep the index outside the source directory")
        source = SourceConfig(id=source_id or f"{harness}:{format}:{root}", harness=harness, root=root, format=format)
        known = next((s for s in self.sources() if s.root == source.root and s.format == source.format), None)
        if known is not None and source_id is None:
            if known.harness != source.harness:
                raise ValueError(f"{root} is already registered as {known.harness}")
            source = known
        else:
            self._index.register_source(source)
        if sync:
            self.sync(source_ids=[source.id])
        return source

    def sources(self):
        return self._index.list_sources()

    def sync(self, *, source_ids=None) -> SyncReport:
        return self.store.sync(source_ids=source_ids)

    def list(self, *, harness=None, source=None, since=None, until=None):
        """Session records, newest first. `since`/`until` accept datetimes or ISO strings (naive = UTC)."""

        def moment(value):
            value = datetime.fromisoformat(value) if isinstance(value, str) else value
            return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value

        filters = SearchFilters(
            harnesses=[harness] if isinstance(harness, str) else list(harness or []),
            source_ids=[source] if isinstance(source, str) else list(source or []),
            started_after=moment(since),
            started_before=moment(until),
        )
        records = list(self._index.iter_sessions(filters))
        return sorted(records, key=lambda s: (s.started_at or "", s.id), reverse=True)

    def resolve(self, reference=None):
        """Map an indexed ID, native ID, collector run ID, or unique prefix to an indexed ID."""
        if reference is None:
            latest = self.list()
            if not latest:
                raise LookupError("No sessions are indexed; add a source first")
            return latest[0].id
        rows = self._index.connection.execute("SELECT id, native_id FROM sessions").fetchall()
        exact = [r["id"] for r in rows if reference in (r["id"], r["native_id"])]
        if len(exact) == 1:
            return exact[0]
        compact = _compact(reference)
        native = {self._run_native_id(reference)} - {None}
        matches = {
            r["id"]
            for r in rows
            if r["id"].startswith(reference) or _compact(r["native_id"]).startswith(compact) or r["native_id"] in native
        }
        if len(matches) != 1:
            problem = "matches no indexed session" if not matches else f"is ambiguous ({len(matches)} sessions)"
            raise LookupError(f"Session reference {reference!r} {problem}")
        return matches.pop()

    def get(self, reference=None):
        """The immutable Session for any reference; None selects the most recent session."""
        return self.store.get(self.resolve(reference))

    def _batches(self):
        for source in self.sources():
            if source.root.name == "sessions" and (source.root.parent / "runs").is_dir():
                yield source.root.parent

    def _run_native_id(self, run_id):
        for batch in self._batches():
            manifest = batch / "runs" / run_id / "manifest.json"
            if manifest.is_file():
                return json.loads(manifest.read_text(encoding="utf-8")).get("thread_id")
        return None

    def run(self, session):
        """Collector run directory and manifest that produced this session, if any."""
        session = session if hasattr(session, "metadata") else self.get(session)
        source = next((s for s in self.sources() if s.id == session.metadata.source_id), None)
        if source is None or source.root.name != "sessions":
            return None
        for directory, manifest in _batch_runs(source.root.parent):
            if manifest.get("thread_id") == session.metadata.native_id:
                return directory, manifest
        return None

    def agent(self, session):
        """The agent configuration recorded before the run (collector batches only)."""
        run = self.run(session)
        if run is None or not (run[0] / "agent-before.json").is_file():
            return None
        return MetaAgent.model_validate_json((run[0] / "agent-before.json").read_text(encoding="utf-8"))
