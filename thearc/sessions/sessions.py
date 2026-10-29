"""Sessions: the one entry point for collecting, finding, and reading sessions.

`Sessions` registers source directories in a session index, detects the harness
and collector-batch layouts, synchronizes them, and resolves sessions by any
recorded identity. Reads go through read-only snapshots of the index, so a
`Session` keeps its events after later synchronizations or after closing.
Indexing never writes into a source directory.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import TracebackType
from typing import Any, Literal, Self, cast
from uuid import uuid4

from thearc.harnesses import Harness

from .immutable import Record
from .index import SessionIndex
from .install import InstalledSession
from .models import SearchFilters, SessionMetadata, SourceConfig, SyncReport
from .session import Results, SearchHit, Session, query_events, read_only_index, search_filters

DEFAULT_INDEX = Path("~/.thearc/learning.sqlite")
_PROBE_FILES = 20
Moment = datetime | str | None


def _first_record(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as stream:
            for line in islice(stream, 5):
                if line.strip():
                    value = json.loads(line)
                    return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
    return None


def detect_harness(root: str | Path) -> Harness:
    """Infer the harness from a few native files; raise if absent or ambiguous."""
    root = Path(root).expanduser().resolve()
    votes: set[Harness] = set()
    for path in islice(sorted(root.rglob("*.jsonl")), _PROBE_FILES):
        if path.name == "transcript.jsonl":
            votes.add("antigravity")
        elif path.name.startswith("rollout-"):
            votes.add("codex")
        elif (record := _first_record(path)) is None:
            continue
        elif isinstance(record.get("info"), dict) and isinstance(record.get("messages"), list):
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


def _batch_runs(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Collector batches store runs/<id>/manifest.json beside the sessions/ transcripts."""
    for path in sorted((root / "runs").glob("*/manifest.json")):
        try:
            yield path.parent, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def _compact(value: str) -> str:
    return value.replace("-", "").lower()


def _moment(value: Moment) -> datetime | None:
    """A datetime or ISO string; naive values are UTC."""
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _names(value: str | Iterable[str] | None) -> builtins.list[str]:
    return [value] if isinstance(value, str) else list(value or [])


class Sessions:
    """Collect, list, and resolve sessions from many harnesses in one index.

        with Sessions() as sessions:
            sessions.add("~/.codex/sessions")
            sessions.add("logs/batch")          # a collector batch
            trajectory = sessions.get("14a6b5fe").trajectories[0]

    The first read after a synchronization copies the index into ``artifact_dir``;
    sessions and results keep reading that snapshot.
    """

    def __init__(self, index_path: str | Path = DEFAULT_INDEX, *, artifact_dir: str | Path | None = None):
        self.path = Path(index_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = (
            Path(artifact_dir).expanduser().resolve() if artifact_dir else self.path.with_suffix(".artifacts")
        )
        self.index = SessionIndex(self.path)
        self._snapshot: Path | None = None
        self._revision: tuple | None = None

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    # --- sources ------------------------------------------------------------------------------------------------

    def add(
        self,
        path: str | Path,
        *,
        harness: Harness | None = None,
        source_id: str | None = None,
        format: Literal["native", "dataclaw"] = "native",
        sync: bool = True,
    ) -> SourceConfig:
        """Register a transcript directory or collector batch, and synchronize it unless ``sync=False``."""
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
        if root in self.path.parents:
            raise ValueError("Keep the index outside the source directory")
        source = SourceConfig(id=source_id or f"{harness}:{format}:{root}", harness=harness, root=root, format=format)
        known = next((s for s in self.sources() if s.root == source.root and s.format == source.format), None)
        if known is not None and source_id is None:
            if known.harness != source.harness:
                raise ValueError(f"{root} is already registered as {known.harness}")
            source = known
        else:
            self.index.register_source(source)
        if sync:
            self.sync(source_ids=[source.id])
        return source

    def add_harness_store(self, harness: Harness, project: str | Path, *, home: str | Path | None = None,
                          sync: bool = True) -> builtins.list[SourceConfig]:
        """Register where ``harness`` keeps the sessions run in ``project`` (see ``thearc.sessions.stores``) and
        synchronize them; returns the sources. OpenCode's sessions are exported next to the index first, so call it
        again to pick up new ones. List them with ``list(cwd=project)``."""
        from .stores import export_opencode_sessions, store_folders

        if harness == "opencode":
            exports = self.artifact_dir / "opencode-exports" / Path(project).resolve().as_posix().strip("/")
            export_opencode_sessions(project, exports)
            folders = [exports]
        else:
            folders = store_folders(harness, project, home=home)
        return [self.add(folder, harness=harness, sync=sync) for folder in folders]

    def ingest(self, source: SourceConfig) -> SyncReport:
        """Register an explicit source configuration and synchronize only it."""
        return self.index.ingest(source)

    def sources(self) -> builtins.list[SourceConfig]:
        return self.index.list_sources()

    def sync(self, *, source_ids: builtins.list[str] | None = None) -> SyncReport:
        return self.index.sync(source_ids=source_ids)

    # --- finding sessions ---------------------------------------------------------------------------------------

    def list(
        self,
        *,
        harness: str | Iterable[str] | None = None,
        source: str | Iterable[str] | None = None,
        since: Moment = None,
        until: Moment = None,
        cwd: str | Path | None = None,
        without_call: str | None = None,
    ) -> builtins.list[SessionMetadata]:
        """Session records, newest first. ``cwd`` keeps the sessions run in that folder or below it;
        ``without_call`` drops those with a tool call whose arguments contain that text (e.g. ``thearc evolve``)."""
        filters = SearchFilters(
            harnesses=cast("list[Harness]", _names(harness)),
            source_ids=_names(source),
            started_after=_moment(since),
            started_before=_moment(until),
            cwd=str(Path(cwd).expanduser().resolve()) if cwd is not None else None,
            without_call=without_call,
        )
        return sorted(self.index.iter_sessions(filters), key=lambda s: (s.started_at or "", s.id), reverse=True)

    def resolve(self, reference: str | None = None) -> str:
        """Map an indexed ID, native ID, collector run ID, or unique prefix to an indexed ID; None means latest."""
        if reference is None:
            latest = self.list()
            if not latest:
                raise LookupError("No sessions are indexed; add a source first")
            return latest[0].id
        rows = self.index.connection.execute("SELECT id, native_id FROM sessions").fetchall()
        exact = [r["id"] for r in rows if reference in (r["id"], r["native_id"])]
        if len(exact) == 1:
            return exact[0]
        compact, native = _compact(reference), self._run_native_id(reference)
        matches = {
            r["id"]
            for r in rows
            if r["id"].startswith(reference)
            or _compact(r["native_id"]).startswith(compact)
            or (native and r["native_id"] == native)
        }
        if len(matches) != 1:
            problem = "matches no indexed session" if not matches else f"is ambiguous ({len(matches)} sessions)"
            raise LookupError(f"Session reference {reference!r} {problem}")
        return matches.pop()

    def get(self, reference: str | None = None) -> Session:
        """The immutable Session for any reference; None selects the most recent session."""
        return self._get(self._capture(), self.resolve(reference))

    def search(
        self, text: str | None = None, *, mode: str = "literal", case_sensitive: bool | None = None, **filters: Any
    ) -> Results:
        """Events matching ``text`` and/or filters (``kind=``, ``status=``, ``tool_name=``, …), in recorded order."""
        selection = search_filters(filters)
        sensitive = (mode == "literal") if case_sensitive is None else case_sensitive

        def generate() -> Iterator[SearchHit]:
            path = self._capture()
            index = read_only_index(path)
            sessions: dict[str, Session] = {}
            try:
                for row in query_events(index, text, selection, mode, sensitive):
                    if row["session_id"] not in sessions:
                        sessions[row["session_id"]] = self._get(path, row["session_id"])
                    event = Record(index.get_event(row["id"]).model_dump(mode="json"))
                    ranges = (
                        SessionIndex._literal_ranges(event.text, text, sensitive) if text and mode == "literal" else []
                    )
                    yield SearchHit(event, sessions[row["session_id"]], tuple(ranges))
            finally:
                index.close()

        return Results(generate)

    def match(self, pattern: Any, **filters: Any) -> Results:
        """Behavioral pattern matches (see ``scanner``) in the sessions the filters select."""
        selection = search_filters(filters)

        def generate() -> Iterator[Any]:
            path = self._capture()
            index = read_only_index(path)
            try:
                for record in index.iter_candidate_sessions(selection):
                    yield from self._get(path, record.id).match(pattern)
            finally:
                index.close()

        return Results(generate)

    # --- collector batches and installation ---------------------------------------------------------------------

    def run(self, session: Session | str) -> tuple[Path, dict[str, Any]] | None:
        """The collector run directory and manifest that produced this session, if any."""
        session = session if isinstance(session, Session) else self.get(session)
        source = next((s for s in self.sources() if s.id == session.metadata.source_id), None)
        if source is None or source.root.name != "sessions":
            return None
        return next(
            (
                (directory, manifest)
                for directory, manifest in _batch_runs(source.root.parent)
                if manifest.get("thread_id") == session.metadata.native_id
            ),
            None,
        )

    def install_all(
        self,
        harness: str,
        *,
        references: Iterable[str] | None = None,
        source_harness: str | Iterable[str] | None = None,
        source: str | Iterable[str] | None = None,
        since: Moment = None,
        until: Moment = None,
        home: str | Path | None = None,
        overwrite: bool = False,
        strict: bool = True,
        keep_id: bool = False,
    ) -> builtins.list[tuple[Any, InstalledSession | Exception]]:
        """Install the given sessions, or every one matching the filters, into ``harness``.

        Returns ``(session metadata, InstalledSession or the exception)`` per session;
        one failure does not stop the others.
        """
        records = (
            [self.get(reference).metadata for reference in references]
            if references
            else self.list(harness=source_harness, source=source, since=since, until=until)
        )
        results: builtins.list[tuple[Any, InstalledSession | Exception]] = []
        for record in records:
            try:
                results.append(
                    (
                        record,
                        self.get(record.id).install(
                            harness, home=home, overwrite=overwrite, strict=strict, keep_id=keep_id
                        ),
                    )
                )
            except (ValueError, OSError) as exc:
                results.append((record, exc))
        return results

    def _run_native_id(self, run_id: str) -> str | None:
        for source in self.sources():
            manifest = source.root.parent / "runs" / run_id / "manifest.json"
            if source.root.name == "sessions" and manifest.is_file():
                return json.loads(manifest.read_text(encoding="utf-8")).get("thread_id")
        return None

    # --- snapshots ----------------------------------------------------------------------------------------------

    def _capture(self) -> Path:
        """A read-only copy of the index, refreshed when it changed (also by another connection)."""
        revision = (
            self.index.revision,
            self.index.connection.total_changes,
            self.index.connection.execute("PRAGMA data_version").fetchone()[0],
        )
        if self._snapshot is None or revision != self._revision:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.artifact_dir, suffix=".sqlite", delete=False) as stream:
                temporary = Path(stream.name)
            destination = sqlite3.connect(temporary)
            try:
                self.index.connection.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
            finally:
                destination.close()
            path = self.artifact_dir / f"{uuid4().hex}.sqlite"
            temporary.rename(path)
            self._snapshot, self._revision = path, revision
        return self._snapshot

    @staticmethod
    def _get(path: Path, session_id: str) -> Session:
        index = read_only_index(path)
        try:
            metadata = index.get_session(session_id)
        finally:
            index.close()
        return Session(Record(metadata.model_dump(mode="json")), f"{path.stem}:{session_id}", path)
