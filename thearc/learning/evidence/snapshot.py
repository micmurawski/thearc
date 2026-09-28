"""Portable, content-verified evidence values. No inference or source-file replay."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from thearc.learning.privacy import hide_historical_ranks
from thearc.learning.privacy import redact as redact_content
from thearc.learning.sessions.models import Event, SearchFilters, Session

SCHEMA_VERSION = 1
POLICY_VERSION = "evidence-1"
ALLOWED_KINDS = frozenset({"message", "tool_call", "tool_result"})
EVENT_FIELDS = frozenset({
    "id", "source_id", "harness", "session_id", "session_native_id", "run_id", "native_id",
    "parent_native_id", "timestamp", "kind", "role", "text", "tool_name", "action_kind",
    "call_id", "status", "arguments", "parent_run_id", "native_type", "reference",
})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _records_digest(manifest: dict, sessions: tuple[str, ...], events: tuple[str, ...]) -> str:
    # Database revisions and local root guards are provenance, not evidence content.
    content = {k: v for k, v in manifest.items() if k not in {
        "content_sha256", "source_revision", "blocked_root_hashes",
    }}
    digest = hashlib.sha256(canonical_json(content).encode())
    for kind, records in (("session", sessions), ("event", events)):
        for record in records:
            digest.update(("\n" + kind + ":" + canonical_json(json.loads(record))).encode())
    return digest.hexdigest()


def check_destination(path: str | Path, blocked_roots: tuple[str, ...] = ()) -> Path:
    """Reject symlink components and source-root descendants before any writes."""
    path = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Destination must not contain symlinks")
    path = path.resolve()
    if any(content_hash(str(part)) in blocked_roots for part in (path, *path.parents)):
        raise ValueError("Destination must be outside source roots")
    if path.exists():
        raise FileExistsError(path)
    return path


def write_json_exclusive(path: Path, value: Any) -> None:
    """Publish a complete file atomically, without replacing an existing target."""
    fd, temporary = tempfile.mkstemp(prefix=".thearc-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """Immutable canonical records; public reads decode fresh defensive copies.

    Use SessionStore.snapshot() or load(). Neither this value nor its exports
    contain live database handles or require the original source files.
    """

    _manifest: str = field(repr=False)
    _sessions: tuple[str, ...] = field(repr=False)
    _events: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._manifest, str) or not isinstance(self._sessions, tuple) or not isinstance(
            self._events, tuple
        ) or not all(isinstance(x, str) for x in (*self._sessions, *self._events)):
            raise TypeError("Snapshot internals must be immutable serialized records")
        manifest = self.manifest
        if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("policy_version") != POLICY_VERSION:
            raise ValueError("Unsupported evidence schema or policy")
        sessions = {s["id"]: Session.model_validate(s) for s in self.sessions}
        if len(sessions) != len(self._sessions):
            raise ValueError("Duplicate session identity")
        ids = set()
        for data in self.iter_events():
            if set(data) - EVENT_FIELDS or data["kind"] not in ALLOWED_KINDS:
                raise ValueError("Unsupported evidence event fields or kind")
            event = Event.model_validate(data)
            session = sessions.get(event.session_id)
            if (session is None or session.source_id != event.source_id or session.harness != event.harness
                    or event.id in ids or event.role in {"system", "developer"}):
                raise ValueError("Invalid event provenance or identity")
            ids.add(event.id)
        if manifest.get("session_count") != len(sessions) or manifest.get("event_count") != len(ids):
            raise ValueError("Snapshot record count mismatch")
        if manifest.get("content_sha256") != self._digest():
            raise ValueError("Snapshot content hash mismatch")

    @property
    def manifest(self) -> dict:
        return json.loads(self._manifest)

    @property
    def sessions(self) -> list[dict]:
        return [json.loads(record) for record in self._sessions]

    @property
    def events(self) -> list[dict]:
        return list(self.iter_events())

    @property
    def sha256(self) -> str:
        return self.manifest["content_sha256"]

    def iter_events(self):
        for record in self._events:
            yield json.loads(record)

    def _digest(self) -> str:
        return _records_digest(self.manifest, self._sessions, self._events)

    def save(self, destination: str | Path) -> Path:
        """Stream records to a new directory; publish the completion manifest last."""
        path = check_destination(destination, tuple(self.manifest["blocked_root_hashes"]))
        path.mkdir(parents=True, exist_ok=False)
        digest = hashlib.sha256()
        with (path / "records.jsonl").open("xb") as stream:
            for kind, records in (("session", self._sessions), ("event", self._events)):
                for record in records:
                    line = (f'{{"type":"{kind}","data":{record}}}\n').encode()
                    digest.update(line)
                    stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        write_json_exclusive(path / "manifest.json", {
            **self.manifest, "records_sha256": digest.hexdigest(),
        })
        return path

    @classmethod
    def load(cls, directory: str | Path, *, max_bytes: int = 100_000_000) -> EvidenceSnapshot:
        path = Path(directory)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        paths = [path / "manifest.json", path / "records.jsonl"]
        if any(p.is_symlink() or any(a.is_symlink() for a in p.parents) for p in paths):
            raise ValueError("Snapshot paths must not be symlinks")
        if sum(p.stat().st_size for p in paths) > max_bytes:
            raise ValueError("Snapshot exceeds storage limit")
        manifest = json.loads(paths[0].read_text(encoding="utf-8"))
        records = {"session": [], "event": []}
        digest = hashlib.sha256()
        size = paths[0].stat().st_size
        with paths[1].open("rb") as stream:
            for line in stream:
                size += len(line)
                if size > max_bytes:
                    raise ValueError("Snapshot exceeds storage limit")
                digest.update(line)
                record = json.loads(line)
                if set(record) != {"type", "data"} or record["type"] not in records:
                    raise ValueError("Invalid snapshot record")
                records[record["type"]].append(canonical_json(record["data"]))
        if manifest.pop("records_sha256", None) != digest.hexdigest():
            raise ValueError("Snapshot file hash mismatch")
        return cls(canonical_json(manifest), tuple(records["session"]), tuple(records["event"]))


def snapshot_store(store, *, session_ids: list[str] | None, filters: SearchFilters | None = None,
                   redact: bool, hide_ranks: bool,
                   kinds: list[str] | None, max_bytes: int) -> EvidenceSnapshot:
    if (session_ids is None) == (filters is None):
        raise ValueError("Provide exactly one of session_ids or filters")
    if filters is not None:
        unsupported = [name for name in ("run_ids", "kinds", "roles", "action_kinds", "tool_names", "statuses")
                       if getattr(filters, name)]
        if unsupported:
            raise ValueError(f"Snapshot filters select whole sessions; unsupported event filters: {unsupported}")
    selected_kinds = ALLOWED_KINDS if kinds is None else frozenset(kinds)
    if session_ids == [] or not selected_kinds or selected_kinds - ALLOWED_KINDS or max_bytes < 1:
        raise ValueError("Select sessions, supported evidence kinds, and a positive storage limit")
    if store.connection.in_transaction:
        raise ValueError("Finish the active database transaction before snapshotting")

    def sanitize(value):
        if hide_ranks:
            value = hide_historical_ranks(value)
        return redact_content(value) if redact else value

    sessions, events, omitted, provenance = [], [], [], []
    size = 0

    def append_record(target, value):
        nonlocal size
        record = canonical_json(value)
        size += len(record.encode())
        if size > max_bytes:
            raise ValueError("Snapshot exceeds storage limit; explicitly select less evidence")
        target.append(record)

    store.connection.execute("BEGIN")
    try:
        revision = store.revision
        ids = (sorted(set(session_ids)) if session_ids is not None
               else sorted(s.id for s in store.iter_sessions(filters)))
        if not ids:
            raise ValueError("No sessions match the snapshot filters")
        sources = {source.id: source for source in store.list_sources()}
        selected_sources = set()
        for session_id in ids:
            session = store.get_session(session_id)
            selected_sources.add(session.source_id)
            data = session.model_dump(mode="json")
            data.update(cwd=None, metadata={})
            append_record(sessions, sanitize(data))
            # IDs are content identities, not chronological ordering keys.
            rows = store.connection.execute(
                "SELECT data FROM events WHERE session_id=? "
                "ORDER BY COALESCE(json_extract(data,'$.timestamp'),''),path,byte_offset,subrecord,id",
                (session_id,),
            )
            for row in rows:
                event = Event.model_validate_json(row[0])
                if event.kind not in selected_kinds or event.role in {"system", "developer"}:
                    omitted.append({"id": event.id, "session_id": session_id,
                                    "reason": "excluded_role" if event.role in {"system", "developer"}
                                    else "excluded_kind"})
                    continue
                source = sources[event.source_id]
                try:
                    relative = str(event.reference.path.relative_to(source.root))
                except ValueError:
                    relative = str(event.reference.path.name)
                data = event.model_dump(mode="json", include=EVENT_FIELDS)
                data["reference"]["path"] = "artifact-" + content_hash([event.source_id, relative])
                # Original record hashes retain traceability without exposing its raw payload.
                provenance.append({"event_id": event.id, "raw_sha256": content_hash(event.raw)})
                append_record(events, sanitize(data))
        manifest = {
            "schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION,
            "source_revision": revision, "session_count": len(sessions), "event_count": len(events),
            "policy": {"redact": redact, "hide_ranks": hide_ranks, "kinds": sorted(selected_kinds)},
            "ordering": "session ID, timestamp, source artifact, byte offset, subrecord, event ID",
            "omitted_events": omitted, "provenance": provenance,
            "sources": [sanitize({"id": source_id, "harness": sources[source_id].harness,
                                   "format": sources[source_id].format}) for source_id in sorted(selected_sources)],
            "blocked_root_hashes": sorted(content_hash(str(s.root)) for s in sources.values()),
            "limitations": ["Raw/internal records excluded; attachments and hidden reasoning are not materialized.",
                            "Evidence reflects indexed data, not a fresh source scan.",
                            "Redaction and historical-rank removal are best effort."],
        }
    finally:
        store.connection.rollback()  # End the read transaction; never commit caller writes.
    if size + len(canonical_json(manifest).encode()) > max_bytes:
        raise ValueError("Snapshot exceeds storage limit; explicitly select less evidence")
    manifest["content_sha256"] = _records_digest(manifest, tuple(sessions), tuple(events))
    return EvidenceSnapshot(canonical_json(manifest), tuple(sessions), tuple(events))
