"""Local SQLite history index. Source transcripts are never modified."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

from thearc.learning.sessions.adapters import get_adapter
from thearc.learning.sessions.models import (
    Event,
    Run,
    RunTrace,
    SearchFilters,
    SearchHit,
    SearchPage,
    SearchQuery,
    Session,
    SourceConfig,
    SourceReference,
    SyncReport,
)

if TYPE_CHECKING:
    from .trajectories import TrajectorySplit


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('revision', 0);
INSERT OR IGNORE INTO settings VALUES ('schema_version', 1);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(
 source_id TEXT NOT NULL, path TEXT NOT NULL, generation INTEGER NOT NULL,
 offset INTEGER NOT NULL, line INTEGER NOT NULL, digest TEXT NOT NULL,
 metadata TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(source_id,path));
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
 harness TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
 kind TEXT NOT NULL, role TEXT, action_kind TEXT, tool_name TEXT, status TEXT,
 call_id TEXT, native_id TEXT, parent_native_id TEXT, parent_run_id TEXT,
 byte_offset INTEGER NOT NULL, subrecord INTEGER NOT NULL, text TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_scope ON events(source_id,session_id,run_id);
CREATE INDEX IF NOT EXISTS events_session_order ON events(session_id,source_id,path,byte_offset,subrecord,id);
CREATE INDEX IF NOT EXISTS events_calls ON events(run_id,call_id,kind);
CREATE INDEX IF NOT EXISTS events_native ON events(run_id,native_id);
CREATE INDEX IF NOT EXISTS events_parent ON events(parent_run_id);
CREATE INDEX IF NOT EXISTS events_stream_order ON events(source_id,session_id,path,byte_offset,subrecord,id);
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, harness TEXT NOT NULL,
 native_id TEXT NOT NULL, title TEXT, cwd TEXT, started_at TEXT, ended_at TEXT,
 event_count INTEGER NOT NULL, run_count INTEGER NOT NULL,
 parent_session_id TEXT, metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_source ON sessions(source_id, harness, native_id);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS sessions_order ON sessions(COALESCE(ended_at,''),id);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source_id TEXT NOT NULL,
 harness TEXT NOT NULL, parent_run_id TEXT, started_at TEXT, ended_at TEXT,
 event_count INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS event_text USING fts5(event_id UNINDEXED,text);
CREATE TABLE IF NOT EXISTS diagnostics(
 source_id TEXT NOT NULL, path TEXT NOT NULL, line INTEGER, message TEXT NOT NULL);
"""


def prefix_hasher(path: Path, length: int):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = length
        while remaining:
            block = stream.read(min(remaining, 1024 * 1024))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    return digest


def prefix_digest(path: Path, length: int) -> str:
    return prefix_hasher(path, length).hexdigest()


def _utc_timestamp(value: str | None) -> str | None:
    """Canonical sortable UTC; never infer a timezone from the host machine."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        return None


class SessionStore:
    """Single-user service. Use one instance per thread; close it after use."""

    def __init__(self, index_path: str | Path):
        path = Path(index_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.create_function("utc_timestamp", 1, _utc_timestamp, deterministic=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        try:
            self.connection.executescript(SCHEMA)
        except sqlite3.OperationalError as exc:
            self.connection.close()
            if "no such module: fts5" in str(exc):
                raise RuntimeError("History indexing requires SQLite with FTS5 support") from exc
            raise
        version = self.connection.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
        if version != 1:
            self.connection.close()
            raise RuntimeError(f"Unsupported history schema version: {version}")
        # Backfill older projections once from indexed events, not source files.
        # Normalization must precede MIN/MAX: mixed offsets sort incorrectly.
        normalized = self.connection.execute(
            "SELECT value FROM settings WHERE key='projection_timestamps_utc'",
        ).fetchone()
        if (
            normalized is None
            or (
                self.connection.execute("SELECT 1 FROM events LIMIT 1").fetchone()
                and not self.connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
            )
        ):
            self._refresh_projections()

    @classmethod
    def open(cls, index_path: str | Path) -> SessionStore:
        return cls(index_path)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def revision(self) -> int:
        return self.connection.execute("SELECT value FROM settings WHERE key='revision'").fetchone()[0]

    def register_source(self, source: SourceConfig) -> None:
        row = self.connection.execute("SELECT config FROM sources WHERE id=?", (source.id,)).fetchone()
        if row and SourceConfig.model_validate_json(row[0]) != source:
            raise ValueError("Source ID already registered with different configuration; use a new source ID")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (source.id, source.model_dump_json()))

    def list_sources(self) -> list[SourceConfig]:
        """Read registered roots without scanning or modifying the source corpus."""
        return [SourceConfig.model_validate_json(row[0]) for row in
                self.connection.execute("SELECT config FROM sources ORDER BY id")]

    def ingest(self, source: SourceConfig) -> SyncReport:
        """Register and synchronize only this source, leaving other sources untouched."""
        self.register_source(source)
        return self.sync(source_ids=[source.id])

    def snapshot(self, *, session_ids: list[str] | None = None, filters: SearchFilters | None = None,
                 redact: bool = True, hide_ranks: bool = True,
                 kinds: list[str] | None = None, max_bytes: int = 100_000_000):
        """Freeze explicit IDs or filtered whole sessions in one read transaction.

        Supply exactly one selector; SearchFilters() explicitly selects all sessions.
        Date filters use indexed session start times, not individual event times.
        No source sync or agent invocation occurs.
        """
        from thearc.learning.evidence.snapshot import snapshot_store

        return snapshot_store(self, session_ids=session_ids, filters=filters, redact=redact, hide_ranks=hide_ranks,
                              kinds=kinds, max_bytes=max_bytes)

    def sync(self, *, source_ids: list[str] | None = None) -> SyncReport:
        report = SyncReport()
        sources = self.list_sources()
        if source_ids is not None:
            unknown = set(source_ids) - {source.id for source in sources}
            if unknown:
                raise ValueError(f"Unknown sources: {sorted(unknown)}")
            sources = [source for source in sources if source.id in source_ids]
        for source in sources:
            if not source.root.is_dir():
                report.warnings.append(f"Unavailable source root: {source.id}: {source.root}")
                # A failed root scan never implies deletion of its histories.
                with self.connection:
                    changed = self.connection.execute(
                        "UPDATE artifacts SET available=0 WHERE source_id=? AND available=1", (source.id,)
                    ).rowcount
                    if changed:
                        self.connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")
                continue
            try:
                paths = list(get_adapter(source).discover(source))
            except OSError as exc:
                report.warnings.append(f"Discovery failed for {source.id}: {exc}")
                continue
            seen = set()
            for path in paths:
                if not path.is_file() or path.is_symlink():
                    continue
                seen.add(str(path))
                try:
                    self._sync_artifact(source, path, report)
                except OSError as exc:
                    report.warnings.append(f"Unable to read {path}: {exc}")
            with self.connection:
                for row in self.connection.execute("SELECT path FROM artifacts WHERE source_id=?", (source.id,)):
                    if row[0] not in seen:
                        changed = self.connection.execute(
                            "UPDATE artifacts SET available=0 WHERE source_id=? AND path=? AND available=1",
                            (source.id, row[0]),
                        ).rowcount
                        if changed:
                            self.connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")
        self._refresh_projections()
        return report

    def _refresh_projections(self) -> None:
        """Rebuild the small session/run projections from canonical events."""
        with self.connection:
            self.connection.execute("DELETE FROM runs")
            self.connection.execute("DELETE FROM sessions")
            self.connection.execute(
                """INSERT INTO sessions
                   (id,source_id,harness,native_id,title,cwd,started_at,ended_at,
                    event_count,run_count,parent_session_id,metadata)
                   SELECT e.session_id,e.source_id,e.harness,
                    COALESCE(MAX(json_extract(e.data,'$.session_native_id')),e.session_id),
                    NULL,MAX(json_extract(e.data,'$.cwd')),
                    MIN(utc_timestamp(json_extract(e.data,'$.timestamp'))),
                    MAX(utc_timestamp(json_extract(e.data,'$.timestamp'))),
                    COUNT(*),COUNT(DISTINCT e.run_id),NULL,'{}'
                   FROM events e GROUP BY e.session_id,e.source_id,e.harness"""
            )
            self.connection.execute(
                """INSERT INTO runs
                   (id,session_id,source_id,harness,parent_run_id,started_at,ended_at,event_count)
                   SELECT e.run_id,e.session_id,e.source_id,e.harness,
                    MAX(NULLIF(e.parent_run_id,'')),
                    MIN(utc_timestamp(json_extract(e.data,'$.timestamp'))),
                    MAX(utc_timestamp(json_extract(e.data,'$.timestamp'))),COUNT(*)
                   FROM events e GROUP BY e.run_id,e.session_id,e.source_id,e.harness"""
            )

            if not self.connection.execute(
                "SELECT 1 FROM settings WHERE key='projection_timestamps_utc'",
            ).fetchone():
                if self.connection.execute("SELECT 1 FROM events LIMIT 1").fetchone():
                    self.connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")
                self.connection.execute("INSERT INTO settings VALUES ('projection_timestamps_utc',1)")

    def _sync_artifact(self, source: SourceConfig, path: Path, report: SyncReport) -> None:
        adapter = get_adapter(source)
        parser_version = getattr(adapter, "version", "native-1")
        row = self.connection.execute(
            "SELECT * FROM artifacts WHERE source_id=? AND path=?", (source.id, str(path))
        ).fetchone()
        offset, line, generation, meta = 0, 0, 0, {}
        rewrite = False
        if row:
            generation = row["generation"]
            previous_meta = json.loads(row["metadata"])
            rewrite = (
                path.stat().st_size < row["offset"]
                or prefix_digest(path, row["offset"]) != row["digest"]
                or previous_meta.get("_parser_version", "native-1") != parser_version
            )
            if rewrite:
                generation += 1
            else:
                offset, line = row["offset"], row["line"]
                meta = json.loads(row["metadata"])
        added, skipped, partial = 0, 0, False
        observed = prefix_hasher(path, offset)
        if row and not rewrite and observed.hexdigest() != row["digest"]:
            raise OSError("Source changed during checkpoint validation; retry sync")
        with self.connection:
            if rewrite:
                ids = self.connection.execute(
                    "SELECT id FROM events WHERE source_id=? AND path=?", (source.id, str(path))
                ).fetchall()
                self.connection.executemany("DELETE FROM event_text WHERE event_id=?", [(r[0],) for r in ids])
                self.connection.execute("DELETE FROM events WHERE source_id=? AND path=?", (source.id, str(path)))
                self.connection.execute("DELETE FROM diagnostics WHERE source_id=? AND path=?", (source.id, str(path)))
            with path.open("rb") as stream:
                stream.seek(offset)
                while True:
                    start = stream.tell()
                    raw = stream.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        partial = True
                        break
                    line += 1
                    offset = stream.tell()
                    observed.update(raw)
                    if not raw.strip():
                        continue
                    ref = SourceReference(
                        path=path, generation=generation, byte_offset=start, byte_length=len(raw), line=line
                    )
                    try:
                        record = json.loads(raw)
                        if not isinstance(record, dict):
                            raise TypeError("Expected a JSON object")
                        events = adapter.normalize(source, ref, record, meta)
                    except (ValueError, TypeError, OverflowError, OSError) as exc:
                        skipped += 1
                        self.connection.execute(
                            "INSERT INTO diagnostics VALUES (?,?,?,?)",
                            (source.id, str(path), line, f"Malformed/unsupported record: {exc}"),
                        )
                        continue
                    for subrecord, event in enumerate(events):
                        self._insert(event, subrecord)
                        added += 1
            if prefix_digest(path, offset) != observed.hexdigest():
                raise OSError("Source changed during ingestion; transaction rolled back; retry sync")
            meta["_partial"] = partial
            meta["_parser_version"] = parser_version
            self.connection.execute(
                "INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?,1)",
                (source.id, str(path), generation, offset, line, observed.hexdigest(), json.dumps(meta)),
            )
            if added or skipped or rewrite or not row or not row["available"] or row["metadata"] != json.dumps(meta):
                self.connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")
        report.artifacts += 1
        report.events_added += added
        report.records_skipped += skipped
        report.partial_files += int(partial)

    def _insert(self, event: Event, subrecord: int) -> None:
        self.connection.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.id,
                event.source_id,
                str(event.reference.path),
                event.harness,
                event.session_id,
                event.run_id,
                event.kind,
                event.role,
                event.action_kind,
                event.tool_name,
                event.status,
                event.call_id,
                event.native_id,
                event.parent_native_id,
                event.parent_run_id,
                event.reference.byte_offset,
                subrecord,
                event.text,
                event.model_dump_json(),
            ),
        )
        self.connection.execute("INSERT INTO event_text VALUES (?,?)", (event.id, event.text))

    def _enrich(self, event: Event) -> Event:
        if event.kind == "tool_result" and event.call_id:
            row = self.connection.execute(
                "SELECT data FROM events WHERE run_id=? AND call_id=? AND kind='tool_call' "
                "ORDER BY byte_offset LIMIT 1",
                (event.run_id, event.call_id),
            ).fetchone()
            if row:
                call = Event.model_validate_json(row[0])
                event = event.model_copy(
                    update={
                        "action_kind": event.action_kind or call.action_kind,
                        "tool_name": event.tool_name or call.tool_name,
                    }
                )
        return event

    def get_event(self, event_id: str) -> Event:
        row = self.connection.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        return self._enrich(Event.model_validate_json(row[0]))

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            source_id=row["source_id"],
            harness=row["harness"],
            native_id=row["native_id"],
            title=row["title"],
            cwd=row["cwd"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            event_count=row["event_count"],
            run_count=row["run_count"],
            parent_session_id=row["parent_session_id"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Run:
        return Run(**dict(row))

    def get_session(self, session_id: str) -> Session:
        row = self.connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        return self._session_from_row(row)

    @staticmethod
    def _session_date_conditions(filters: SearchFilters) -> tuple[list[str], list[str]]:
        conditions, params = [], []
        for cutoff, operator in ((filters.started_before, "<"), (filters.started_after, ">")):
            if cutoff is not None:
                conditions.append(f"started_at {operator} ?")
                params.append(cutoff.astimezone(UTC).isoformat(timespec="microseconds"))
        return conditions, params

    @staticmethod
    def _session_conditions(filters: SearchFilters | None) -> tuple[str, list]:
        filters = filters or SearchFilters()
        conditions, params = SessionStore._session_date_conditions(filters)
        mapping = {"source_ids": "source_id", "harnesses": "harness"}
        for field, column in mapping.items():
            values = getattr(filters, field)
            if values:
                conditions.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if filters.session_ids:
            conditions.append(f"id IN ({','.join('?' for _ in filters.session_ids)})")
            params.extend(filters.session_ids)
        return " AND ".join(conditions) or "1", params

    def iter_sessions(self, filters: SearchFilters | None = None) -> Iterator[Session]:
        """Stream all matching sessions in list_sessions order, without a row cap.

        A single SELECT cursor avoids Python-side corpus materialization and
        repeated OFFSET scans. It does not freeze event content for later steps.
        Consume/close the iterator before closing or writing through this store.
        """
        where, params = self._session_conditions(filters)
        cursor = self.connection.execute(
            f"SELECT * FROM sessions WHERE {where} ORDER BY COALESCE(ended_at,''),id", params,
        )
        try:
            for row in cursor:
                yield self._session_from_row(row)
        finally:
            cursor.close()

    def iter_candidate_sessions(self, filters: SearchFilters | None = None) -> Iterator[Session]:
        """Select sessions containing an event satisfying all supplied filters.

        Unlike iter_sessions, this also applies event-level filters (tools,
        statuses, runs, etc.). Filters select candidates, not the events later
        loaded for sequence matching. There is no pagination cap.
        """
        conditions, params = self._filters(filters or SearchFilters())
        where = " AND ".join(conditions) or "1"
        cursor = self.connection.execute(
            "SELECT * FROM sessions WHERE id IN ("
            f"SELECT e.session_id FROM events e WHERE {where}) "
            "ORDER BY COALESCE(ended_at,''),id", params,
        )
        try:
            for row in cursor:
                yield self._session_from_row(row)
        finally:
            cursor.close()

    def list_sessions(self, filters: SearchFilters | None = None, limit: int = 100, offset: int = 0) -> list[Session]:
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValueError("Session pagination must use limit 1..1000 and a non-negative offset")
        where, params = self._session_conditions(filters)
        rows = self.connection.execute(
            f"SELECT * FROM sessions WHERE {where} ORDER BY COALESCE(ended_at,''),id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def list_runs(self, session_id: str, limit: int = 1000, offset: int = 0) -> list[Run]:
        if not self.connection.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise KeyError(session_id)
        rows = self.connection.execute(
            "SELECT * FROM runs WHERE session_id=? ORDER BY COALESCE(started_at,''),id LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def session_stats(self, session_id: str) -> dict[str, int | str]:
        session = self.get_session(session_id)
        return {"session_id": session.id, "event_count": session.event_count, "run_count": session.run_count}

    def read_evidence(self, event_id: str) -> dict:
        """Read the original JSON record, rejecting unavailable or changed evidence."""
        event = self.get_event(event_id)
        reference = event.reference
        with reference.path.open("rb") as stream:
            stream.seek(reference.byte_offset)
            record = json.loads(stream.read(reference.byte_length))
        for component in event.raw_pointer.split("/")[1:]:
            component = component.replace("~1", "/").replace("~0", "~")
            try:
                record = record[int(component)] if isinstance(record, list) else record[component]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError("Source evidence has changed; source object is no longer available") from exc
        if record != event.raw:
            raise ValueError("Source evidence has changed; synchronize before citing it")
        return record

    def _filters(self, filters: SearchFilters) -> tuple[list[str], list]:
        conditions, params = [], []
        date_conditions, date_params = self._session_date_conditions(filters)
        if date_conditions:
            conditions.append("e.session_id IN (SELECT id FROM sessions WHERE " + " AND ".join(date_conditions) + ")")
            params.extend(date_params)
        columns = {
            "source_ids": "source_id",
            "harnesses": "harness",
            "session_ids": "session_id",
            "run_ids": "run_id",
            "kinds": "kind",
            "roles": "role",
            "statuses": "status",
            "action_kinds": "action_kind",
            "tool_names": "tool_name",
        }
        for field, column in columns.items():
            values = getattr(filters, field)
            if not values:
                continue
            expr = f"e.{column}"
            if column in {"action_kind", "tool_name"}:
                expr = (
                    f"COALESCE(e.{column}, (SELECT c.{column} FROM events c WHERE c.run_id=e.run_id "
                    "AND c.call_id=e.call_id AND c.kind='tool_call' ORDER BY c.byte_offset LIMIT 1))"
                )
            conditions.append(f"{expr} IN ({','.join('?' for _ in values)})")
            params.extend(values)
        return conditions, params

    def _warnings(self) -> list[str]:
        unavailable = self.connection.execute("SELECT count(*) FROM artifacts WHERE available=0").fetchone()[0]
        skipped = self.connection.execute("SELECT count(*) FROM diagnostics").fetchone()[0]
        warnings = []
        if unavailable:
            warnings.append(f"{unavailable} source artifacts unavailable; retained indexed evidence is searchable")
        if skipped:
            warnings.append(f"{skipped} records were skipped; inspect ingestion diagnostics")
        partial = sum(
            bool(json.loads(row[0]).get("_partial"))
            for row in self.connection.execute("SELECT metadata FROM artifacts WHERE available=1")
        )
        if partial:
            warnings.append(f"{partial} artifacts have incomplete final records awaiting a later sync")
        return warnings

    def search(self, query: SearchQuery) -> SearchPage:
        conditions, params = self._filters(query.filters)
        if not query.include_internal:
            conditions.append("e.kind IN ('message','tool_call','tool_result')")
            conditions.append("COALESCE(e.role,'') NOT IN ('system','developer')")
        score = "NULL AS score"
        join = ""
        order = "e.source_id,e.session_id,e.run_id,e.path,e.byte_offset,e.subrecord,e.id"
        if query.mode == "lexical":
            # Literal token phrase input, not an unrestricted FTS expression.
            join = "JOIN event_text ON event_text.event_id=e.id"
            conditions.append("event_text MATCH ?")
            params.append('"' + query.text.replace('"', '""') + '"')
            score = "-bm25(event_text) AS score"
            order = "score DESC," + order
        else:
            # Python casefold gives explicit Unicode behavior; still a scan, not an inverted index.
            self.connection.create_function(
                "literal_contains", 2, lambda text, term: int(term.casefold() in text.casefold())
            )
            conditions.append("instr(e.text,?)>0" if query.case_sensitive else "literal_contains(e.text,?)=1")
            params.append(query.text)
        where = " AND ".join(conditions) or "1"
        rows = self.connection.execute(
            f"SELECT e.data,a.available,{score} FROM events e {join} "
            "JOIN artifacts a ON a.source_id=e.source_id AND a.path=e.path "
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, query.limit + 1, query.offset),
        ).fetchall()
        hits = []
        for row in rows[: query.limit]:
            event = self._enrich(Event.model_validate_json(row["data"]))
            ranges = (
                self._literal_ranges(event.text, query.text, query.case_sensitive) if query.mode == "literal" else []
            )
            start = max(0, ranges[0][0] - 80) if ranges else 0
            hits.append(
                SearchHit(
                    event=event,
                    snippet=event.text[start : start + 320],
                    match_ranges=ranges,
                    score=row["score"],
                    match_reason=f"{query.mode} match in event text",
                    source_available=bool(row["available"]),
                )
            )
        more = len(rows) > query.limit
        return SearchPage(
            hits=hits,
            has_more=more,
            next_offset=query.offset + query.limit if more else None,
            index_revision=self.revision,
            warnings=self._warnings(),
        )

    @staticmethod
    def _literal_ranges(text: str, term: str, sensitive: bool) -> list[tuple[int, int]]:
        if sensitive:
            haystack, needle = text, term
            mapping = list(range(len(text)))
        else:
            folded, mapping = [], []
            for index, character in enumerate(text):
                value = character.casefold()
                folded.append(value)
                mapping.extend([index] * len(value))
            haystack, needle = "".join(folded), term.casefold()
        result, position = [], 0
        while needle:
            position = haystack.find(needle, position)
            if position < 0:
                break
            result.append((mapping[position], mapping[position + len(needle) - 1] + 1))
            position += len(needle)
        return result

    def get_context(self, event_id: str, before: int = 3, after: int = 3) -> list[Event]:
        if not 0 <= before <= 1000 or not 0 <= after <= 1000:
            raise ValueError("Context bounds must be between 0 and 1000")
        event = self.get_event(event_id)
        if event.harness == "pi" and event.native_id:
            # Follow parentId edges, never chronological siblings from abandoned branches.
            def native_group(native_id):
                return [
                    self._enrich(Event.model_validate_json(r[0]))
                    for r in self.connection.execute(
                        "SELECT data FROM events WHERE run_id=? AND native_id=? ORDER BY path,byte_offset,subrecord",
                        (event.run_id, native_id),
                    )
                ]

            ancestors, parent, seen = [], event.parent_native_id, set()
            while parent and parent not in seen:
                seen.add(parent)
                group = native_group(parent)
                if not group:
                    break
                ancestors = group + ancestors
                parent = group[0].parent_native_id
                if len(ancestors) >= before:
                    break
            group = native_group(event.native_id)
            # Ambiguous descendants require an explicit branch selection; don't guess.
            position = next(i for i, item in enumerate(group) if item.id == event.id)
            prior = ancestors + group[:position]
            return (prior[-before:] if before else []) + group[position : position + after + 1]
        location = self.connection.execute(
            "SELECT path,byte_offset,subrecord FROM events WHERE id=?", (event_id,)
        ).fetchone()
        key = tuple(location)
        prior = self.connection.execute(
            "SELECT data FROM events WHERE run_id=? AND (path,byte_offset,subrecord)<(?,?,?) "
            "ORDER BY path DESC,byte_offset DESC,subrecord DESC LIMIT ?",
            (event.run_id, *key, before),
        ).fetchall()
        following = self.connection.execute(
            "SELECT data FROM events WHERE run_id=? AND (path,byte_offset,subrecord)>(?,?,?) "
            "ORDER BY path,byte_offset,subrecord LIMIT ?",
            (event.run_id, *key, after),
        ).fetchall()
        return (
            [self._enrich(Event.model_validate_json(r[0])) for r in reversed(prior)]
            + [event]
            + [self._enrich(Event.model_validate_json(r[0])) for r in following]
        )

    def get_delegation_trace(self, run_id: str) -> RunTrace:
        row = self.connection.execute("SELECT parent_run_id FROM events WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        children = [
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT run_id FROM events WHERE parent_run_id=? ORDER BY run_id", (run_id,)
            )
        ]
        calls = [
            Event.model_validate_json(r[0])
            for r in self.connection.execute(
                "SELECT data FROM events WHERE run_id=? AND action_kind LIKE 'agent.%' ORDER BY path,byte_offset",
                (run_id,),
            )
        ]
        descendants = [
            r[0]
            for r in self.connection.execute(
                "WITH RECURSIVE descendants(run_id) AS ("
                "SELECT DISTINCT run_id FROM events WHERE parent_run_id=? UNION "
                "SELECT e.run_id FROM events e JOIN descendants d ON e.parent_run_id=d.run_id) "
                "SELECT run_id FROM descendants WHERE run_id<>? ORDER BY run_id",
                (run_id, run_id),
            )
        ]
        return RunTrace(
            run_id=run_id, parent_run_id=row[0], children=children, descendants=descendants, delegation_events=calls
        )

    def diagnostics(self) -> list[dict]:
        return [dict(r) for r in self.connection.execute("SELECT * FROM diagnostics ORDER BY source_id,path,line")]

    def scan_events(self, filters: SearchFilters | None = None, columns=None, batch_size: int = 8192):
        from thearc.learning.sessions.columnar import scan_events

        return scan_events(self, filters, columns, batch_size)

    def export_dataset(self, destination: str | Path, filters: SearchFilters | None = None) -> Path:
        from thearc.learning.sessions.columnar import export_dataset

        return export_dataset(self, destination, filters)

    def split_session(self, session_id: str) -> TrajectorySplit:
        """Split all indexed events of a session into prompt-triggered trajectories."""
        from .trajectories import split_trajectories

        self.get_session(session_id)  # Distinguish a missing session from an empty split.
        return split_trajectories(list(self.iter_events(SearchFilters(session_ids=[session_id]))))

    def iter_events(self, filters: SearchFilters | None = None) -> Iterator[Event]:
        """Yield native stream order, never hash-ID order.

        Group by source/session/artifact, preserving byte offsets and normalized
        subrecord order within each artifact, even when timestamps are absent or
        regress. Paths order separate artifacts deterministically, not causally.
        """
        conditions, params = self._filters(filters or SearchFilters())
        where = " AND ".join(conditions) or "1"
        cursor = self.connection.execute(
            f"SELECT e.data FROM events e WHERE {where} "
            "ORDER BY e.source_id,e.session_id,e.path,e.byte_offset,e.subrecord,e.id", params,
        )
        try:
            for row in cursor:
                yield self._enrich(Event.model_validate_json(row[0]))
        finally:
            cursor.close()

# Historical service import aliases the same implementation.
HistoryService = SessionStore
