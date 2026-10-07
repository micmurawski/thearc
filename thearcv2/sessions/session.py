"""Sessions as immutable values: events, trajectories, prefixes, search, and exports.

A ``Session`` either holds its events directly (``Session.from_events``) or reads
them lazily from a read-only snapshot of the session index (``Sessions.get``).
Its events are immutable records; nothing here writes to an index or a source.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from itertools import groupby
from pathlib import Path

from .formats import evidence_to_markdown, export_events, immutable_events
from .immutable import Record
from .index import SCHEMA, SessionIndex, _utc_timestamp
from .models import Event, SearchFilters, SessionMetadata
from .trajectories import _stream, split_trajectories


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_only_index(path) -> SessionIndex:
    """A query-only connection to an index snapshot; it never migrates or writes."""
    index = SessionIndex.__new__(SessionIndex)
    index.connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    index.connection.row_factory = sqlite3.Row
    index.connection.create_function("utc_timestamp", 1, _utc_timestamp, deterministic=True)
    return index


def search_filters(options) -> SearchFilters:
    """Keyword filters such as ``kind="tool_call"`` or ``status=["failure"]`` as ``SearchFilters``."""
    aliases = {"kind": "kinds", "role": "roles", "status": "statuses", "tool_name": "tool_names",
               "action_kind": "action_kinds", "session_id": "session_ids", "source_id": "source_ids",
               "run_id": "run_ids", "harness": "harnesses"}
    data = {}
    for key, value in options.items():
        if key in aliases:
            key = aliases[key]
            value = [value] if isinstance(value, str) else value
        if key not in SearchFilters.model_fields:
            raise TypeError(f"Unknown search filter: {key}")
        data[key] = value
    return SearchFilters(**data)


def query_events(index, text, filters, mode, sensitive, ids=None) -> Iterator[sqlite3.Row]:
    """Matching ``(id, session_id)`` rows in recorded order."""
    if mode not in {"literal", "lexical"}:
        raise ValueError("mode must be literal or lexical")
    if text is not None and (not isinstance(text, str) or not text):
        raise ValueError("Search text must be nonempty or None")
    conditions, params = index._filters(filters)
    if ids is not None:
        conditions.append("e.id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(ids))
    if text is not None:
        if mode == "lexical":
            if sensitive:
                raise ValueError("Lexical search is case-insensitive; set case_sensitive=False")
            conditions.append("e.id IN (SELECT event_id FROM event_text WHERE event_text MATCH ?)")
            params.append('"' + text.replace('"', '""') + '"')
        else:
            index.connection.create_function("matches", 2, lambda a, b: int(b.casefold() in a.casefold()))
            conditions.append("instr(e.text,?)>0" if sensitive else "matches(e.text,?)=1")
            params.append(text)
    where = " AND ".join(conditions) or "1"
    cursor = index.connection.execute(
        f"SELECT e.id,e.session_id FROM events e WHERE {where} "
        "ORDER BY e.source_id,e.session_id,e.path,e.byte_offset,e.subrecord,e.id", params,
    )
    try:
        yield from cursor
    finally:
        cursor.close()


class Results(Sequence):
    """Reusable lazy results. Membership is pinned on first evaluation."""

    def __init__(self, factory):
        self._factory = factory
        self._iterator = None
        self._cache = []
        self._done = False

    def _next(self):
        if self._done:
            return False
        if self._iterator is None:
            self._iterator = iter(self._factory())
        try:
            self._cache.append(next(self._iterator))
            return True
        except StopIteration:
            self._done = True
            return False

    def __iter__(self):
        position = 0
        while position < len(self._cache) or self._next():
            yield self._cache[position]
            position += 1

    def __getitem__(self, position):
        if isinstance(position, slice) or position < 0:
            return self.collect()[position]
        while len(self._cache) <= position and self._next():
            pass
        return self._cache[position]

    def __len__(self):
        return self.count()

    def count(self):
        while self._next():
            pass
        return len(self._cache)

    def collect(self):
        self.count()
        return list(self._cache)

    def __repr__(self):
        return f"Results(cached={len(self._cache)}, complete={self._done})"


@dataclass(frozen=True)
class SearchHit:
    """A matching event, the session it belongs to, and where the text matched."""

    event: Record
    session: Session
    match_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def trajectory(self) -> Trajectory | None:
        return next((t for t in self.session.trajectories if any(e.id == self.event.id for e in t.selected_events)),
                    None)


class EventSequence:
    """Rendering, export, search, and pattern matching shared by sessions, trajectories, and views."""

    selected_events: tuple

    def to_markdown(self):
        return evidence_to_markdown(self.selected_events)

    def to_jsonl(self, format="thearc"):
        return export_events(self.selected_events, format).text

    def iter_jsonl(self, format="thearc"):
        # Canonical rendering streams records; provider conversion validates whole relationships.
        if format == "thearc":
            for event in self.selected_events:
                yield json.dumps(event.model_dump(), ensure_ascii=False) + "\n"
        else:
            yield from self.to_jsonl(format).splitlines(keepends=True)

    def to_codex(self):
        return export_events(self.selected_events, "codex")

    def to_claude(self):
        return export_events(self.selected_events, "claude")

    def _session(self) -> Session:
        session = self if isinstance(self, Session) else self.session
        if session is None:
            raise ValueError("This trajectory was split from loose events; it has no session to search")
        return session

    def search(self, text=None, *, mode="literal", case_sensitive=None, **filters) -> Results:
        selection = search_filters(filters)
        sensitive = (mode == "literal") if case_sensitive is None else case_sensitive

        def generate():
            session = self._session()
            index = session._index()
            try:
                by_id = {e.id: e for e in self.selected_events}
                matching = {row["id"] for row in query_events(index, text, selection, mode, sensitive, list(by_id))}
                for event in self.selected_events:
                    if event.id not in matching:
                        continue
                    ranges = (SessionIndex._literal_ranges(event.text, text, sensitive)
                              if text and mode == "literal" else [])
                    yield SearchHit(event, session, tuple(ranges))
            finally:
                index.close()
        return Results(generate)

    def match(self, pattern):
        results = []
        events = [Event.model_validate(e.model_dump()) for e in self.selected_events]
        offset = 0
        for _, group in groupby(events, key=_stream):
            group = list(group)
            for match in pattern.find_matches(group):
                results.append(replace(match, start_index=match.start_index + offset,
                                       end_index=match.end_index + offset,
                                       events=immutable_events(match.events)))
            offset += len(group)
        return tuple(results)


@dataclass(frozen=True, repr=False)
class Trajectory(EventSequence):
    """The activity triggered by one user prompt: the prompt events and the response events that follow.

    ``boundary`` says why it ended: ``completed`` or ``interrupted`` (recorded by the
    harness), or ``next_prompt``, ``end_of_stream``, ``stream_boundary`` (where the
    recording stops, not whether the work succeeded). ``session`` is set when the
    trajectory comes from a ``Session``; trajectories split from loose events have none.
    """

    prompt_events: tuple
    events: tuple
    boundary: str
    native_turn_id: str | None = None
    session: Session | None = field(default=None, compare=False)

    @property
    def id(self) -> str:
        """The ID of the first prompt event: stable within the indexed transcript generation."""
        return self.prompt_events[0].id

    @property
    def session_id(self) -> str:
        return self.prompt_events[0].session_id

    @property
    def run_id(self) -> str:
        return self.prompt_events[0].run_id

    @property
    def prompt_text(self) -> str:
        return "\n".join(e.text for e in self.prompt_events)

    @property
    def event_ids(self) -> list[str]:
        """Response events only; excludes the prompt."""
        return [e.id for e in self.events]

    @property
    def selected_events(self) -> tuple:
        return self.prompt_events + self.events

    def __repr__(self):
        return f"Trajectory(id={self.id!r}, session_id={self.session_id!r}, boundary={self.boundary!r})"


@dataclass(frozen=True)
class TrajectorySplit(Sequence):
    """Lossless partition of events: every event is in a trajectory's prompt or response, or unassigned."""

    trajectories: tuple[Trajectory, ...] = ()
    unassigned_events: tuple = ()

    def __getitem__(self, index):
        return self.trajectories[index]

    def __len__(self):
        return len(self.trajectories)


@dataclass(frozen=True, repr=False)
class Session(EventSequence):
    """One recorded conversation: its metadata and its events in recorded order.

    ``artifact_id`` identifies exactly these events: it changes when the session
    is re-indexed with different content.
    """

    _metadata: Record
    artifact_id: str
    _archive: Path | None = None
    _captured: tuple | None = None

    @property
    def id(self) -> str:
        return self._metadata.id

    @property
    def metadata(self) -> Record:
        return self._metadata

    def __repr__(self):
        return f"Session(id={self.id!r}, artifact_id={self.artifact_id!r})"

    @classmethod
    def from_events(cls, events, *, metadata=None) -> Session:
        captured = immutable_events(events)
        if metadata is None:
            if not captured:
                raise ValueError("Empty sessions require explicit session metadata")
            first = captured[0]
            metadata = SessionMetadata(id=first.session_id, source_id=first.source_id, harness=first.harness,
                                       native_id=first.session_native_id or first.session_id)
        metadata = SessionMetadata.model_validate(
            metadata.model_dump() if hasattr(metadata, "model_dump") else metadata)
        if any(e.session_id != metadata.id or e.source_id != metadata.source_id or e.harness != metadata.harness
               for e in captured):
            raise ValueError("Events must belong to the supplied session")
        if len({e.id for e in captured}) != len(captured):
            raise ValueError("Duplicate event identity")
        data = metadata.model_dump(mode="json")
        data["event_count"] = len(captured)
        data["run_count"] = len({e.run_id for e in captured})
        identity = _hash({"session": data, "events": [e.model_dump() for e in captured]})
        return cls(Record(data), identity, _captured=captured)

    @classmethod
    def open(cls, path) -> Session:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Unsupported session artifact version")
        result = cls.from_events(data["events"], metadata=data["session"])
        if result.artifact_id != data["sha256"]:
            raise ValueError("Session artifact checksum mismatch")
        return result

    def save(self, path) -> Path:
        detached = Session.from_events(self.events, metadata=self.metadata)
        data = {"version": 1, "session": detached.metadata.model_dump(),
                "events": [e.model_dump() for e in detached.events], "sha256": detached.artifact_id}
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        return Path(path)

    @cached_property
    def events(self) -> tuple:
        if self._captured is not None:
            return self._captured
        index = read_only_index(self._archive)
        try:
            return immutable_events(list(index.iter_events(SearchFilters(session_ids=[self.id]))))
        finally:
            index.close()

    @property
    def selected_events(self) -> tuple:
        return self.events

    def _index(self) -> SessionIndex:
        if self._archive is not None:
            return read_only_index(self._archive)
        index = SessionIndex.__new__(SessionIndex)
        index.connection = sqlite3.connect(":memory:")
        index.connection.row_factory = sqlite3.Row
        index.connection.create_function("utc_timestamp", 1, _utc_timestamp, deterministic=True)
        index.connection.executescript(SCHEMA)
        for i, event in enumerate(self.events):
            index._insert(Event.model_validate(event.model_dump()), i)
        index._refresh_projections()
        return index

    @cached_property
    def trajectories(self) -> TrajectorySplit:
        """The session split at user prompts; see ``Trajectory``."""
        split = split_trajectories([Event.model_validate(e.model_dump()) for e in self.events])
        by_id = {e.id: e for e in self.events}
        return TrajectorySplit(
            tuple(replace(t, session=self, prompt_events=tuple(by_id[e.id] for e in t.prompt_events),
                          events=tuple(by_id[e.id] for e in t.events)) for t in split),
            tuple(by_id[e.id] for e in split.unassigned_events))

    def through(self, trajectory: Trajectory) -> SessionView:
        """The session up to and including ``trajectory``: what the agent had seen when it finished."""
        if trajectory.session is None or (trajectory.session.artifact_id, trajectory.session.id) != (
                self.artifact_id, self.id):
            raise ValueError("Trajectory belongs to a different session artifact")
        if not any(t.id == trajectory.id for t in self.trajectories):
            raise ValueError("Unknown trajectory")
        streams = {(e.run_id, e.reference.path, e.reference.generation) for e in self.events}
        if len(streams) > 1:
            raise ValueError("Ambiguous prefix across multiple runs or artifacts")
        last = trajectory.selected_events[-1].id
        endpoint = next(i for i, event in enumerate(self.events) if event.id == last)
        return SessionView(self, endpoint + 1, trajectory.native_turn_id)


@dataclass(frozen=True, repr=False)
class SessionView(EventSequence):
    """A session prefix ending at a trajectory boundary; earlier trajectories included."""

    session: Session
    stop: int
    native_turn_id: str | None = None

    def __post_init__(self):
        if type(self.stop) is not int or not 0 < self.stop <= len(self.session.events):
            raise ValueError("A session prefix requires a valid positive endpoint")
        endings = [t for t in self.session.trajectories
                   if t.selected_events[-1].id == self.session.events[self.stop - 1].id]
        if not endings or endings[0].native_turn_id != self.native_turn_id:
            raise ValueError("A prefix must end at a recorded trajectory boundary")

    @property
    def selected_events(self) -> tuple:
        return self.session.events[:self.stop]

    @property
    def events(self) -> tuple:
        return self.selected_events

    def __repr__(self):
        return f"SessionView(session_id={self.session.id!r}, stop={self.stop})"
