"""Select candidate sessions in SQLite and match complete sequences in Python.

Pattern classes remain importable here for compatibility; their implementations
live in :mod:`thearc.learning.sessions.patterns`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import replace
from itertools import groupby
from typing import TYPE_CHECKING, Any

from thearc.learning.sessions.models import Event, SearchFilters
from thearc.learning.sessions.patterns import (
    AbandonedFix,
    BehavioralPattern,
    EmptyToolResult,
    FrustrationSpike,
    IgnoredUserInstruction,
    LongSilence,
    PatternMatch,
    SelfOverwrite,
    StubbornToolLoop,
    _stream_key,
)
from thearc.learning.sessions.store import SessionStore

if TYPE_CHECKING:
    from thearc.learning.ace.pipeline import SessionBundle

__all__ = [
    "DEFAULT_PATTERNS", "AbandonedFix", "BehavioralPattern", "EmptyToolResult", "FrustrationSpike",
    "IgnoredUserInstruction", "LongSilence", "PatternMatch", "PatternScanner", "SelfOverwrite", "StubbornToolLoop",
]


# ---------------------------------------------------------------------------
# Default pattern registry
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: list[BehavioralPattern] = [
    StubbornToolLoop(),
    FrustrationSpike(),
]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class PatternScanner:
    """Scan indexed sessions for behavioral anti-patterns.

    Parameters
    ----------
    store:
        An open ``SessionStore`` (or ``HistoryService``).
    patterns:
        Optional list of patterns. Defaults to :data:`DEFAULT_PATTERNS`.

    Usage::

        store = SessionStore("index.db")
        scanner = PatternScanner(store)

        # Scan everything
        for match in scanner.scan():
            print(match.pattern_name, match.session_id, match.description)

        # Scan one session
        for match in scanner.scan_session("sess-uuid"):
            print(match)

        # Filter by harness
        from thearc.learning.sessions.models import SearchFilters
        for match in scanner.scan(filters=SearchFilters(harnesses=["claude"])):
            print(match)

        # Only check specific patterns
        scanner = PatternScanner(store, patterns=[StubbornToolLoop(min_repeats=3)])
    """

    def __init__(
        self,
        store: SessionStore | None = None,
        patterns: Sequence[BehavioralPattern] | None = None,
    ) -> None:
        self._store = store
        self._patterns = list(patterns) if patterns is not None else deepcopy(DEFAULT_PATTERNS)

    @property
    def patterns(self) -> list[BehavioralPattern]:
        return list(self._patterns)

    def scan(
        self,
        *,
        filters: SearchFilters | None = None,
        pattern_names: Sequence[str] | None = None,
        **sql_filters: Any,
    ) -> list[PatternMatch]:
        """Scan all sessions matching *filters* for behavioral matches.

        Parameters
        ----------
        filters:
            Optional ``SearchFilters`` to select sessions in SQLite. All event
            conditions must match the same event. Matching then sees the full
            session, including events that did not satisfy these filters.
            Alternatively, pass filter fields directly, e.g. statuses=["failure"].
        pattern_names:
            If given, only run the named subset of patterns.

        Returns
        -------
        list[PatternMatch]
            All matches found, sorted by session then start index.
        """
        if sql_filters:
            if filters is not None:
                raise ValueError("Use either filters or filter keyword arguments, not both")
            unknown = sql_filters.keys() - SearchFilters.model_fields.keys()
            if unknown:
                raise ValueError(f"Unknown filter(s): {sorted(unknown)}")
            filters = SearchFilters(**sql_filters)
        active = self._resolve_patterns(pattern_names)
        all_matches: list[PatternMatch] = []
        for session in self._require_store().iter_candidate_sessions(filters):
            events = list(self._session_events(session.id))
            all_matches.extend(self._match_events(events, active))
        all_matches.sort(key=lambda m: (m.session_id, m.start_index))
        return all_matches

    def scan_session(
        self,
        session_id: str,
        *,
        pattern_names: Sequence[str] | None = None,
    ) -> list[PatternMatch]:
        """Scan a single session by ID.

        Returns
        -------
        list[PatternMatch]
            Matches found, sorted by start index.
        """
        active = self._resolve_patterns(pattern_names)
        events = list(self._session_events(session_id))
        return self._match_events(events, active)

    def scan_events(
        self,
        events: list[Event],
        *,
        pattern_names: Sequence[str] | None = None,
    ) -> list[PatternMatch]:
        """Scan an already-loaded list of events (no store required).

        Useful for scanning translation-extracted events or test fixtures.
        """
        active = self._resolve_patterns(pattern_names)
        return self._match_events(events, active)

    def summary(
        self,
        matches: list[PatternMatch],
    ) -> dict[str, Any]:
        """Aggregate match statistics for reporting.

        Returns a dict with per-pattern counts, severity breakdown, and
        the list of affected session IDs.
        """
        by_pattern: dict[str, int] = {}
        by_severity: dict[str, int] = {"info": 0, "warning": 0, "error": 0}
        session_ids: set[str] = set()
        for m in matches:
            by_pattern[m.pattern_name] = by_pattern.get(m.pattern_name, 0) + 1
            by_severity[m.severity] = by_severity.get(m.severity, 0) + 1
            session_ids.add(m.session_id)
        return {
            "total_matches": len(matches),
            "by_pattern": by_pattern,
            "by_severity": by_severity,
            "sessions_affected": sorted(session_ids),
        }

    # --- internals --------------------------------------------------------

    def _require_store(self) -> SessionStore:
        if self._store is None:
            raise ValueError("An open SessionStore is required; use scan_events for loaded events")
        return self._store

    def _session_events(self, session_id: str) -> Iterator[Event]:
        return self._require_store().iter_events(SearchFilters(session_ids=[session_id]))

    @staticmethod
    def _match_events(events: list[Event], patterns: list[BehavioralPattern]) -> list[PatternMatch]:
        # Never concatenate separate runs/artifacts into an invented sequence.
        # Offsets still refer to the original, complete input list.
        matches: list[PatternMatch] = []
        offset = 0
        for _, group in groupby(events, key=_stream_key):
            segment = list(group)
            for pattern in patterns:
                for match in pattern.find_matches(segment):
                    matches.append(replace(match, start_index=match.start_index + offset,
                                           end_index=match.end_index + offset))
            offset += len(segment)
        matches.sort(key=lambda match: match.start_index)
        return matches

    def reflection_bundle(self, match: PatternMatch) -> SessionBundle:
        """Package the exact indexed slice for evidence-based reflection.

        Records omitted event IDs for coverage. Native fork reflection instead
        uses the original session and a prompt focusing on the matched behavior.
        """
        from thearc.learning.ace.pipeline import SessionBundle

        store = self._require_store()
        session = store.get_session(match.session_id)
        events = list(self._session_events(match.session_id))
        selected = events[match.start_index:match.end_index + 1]
        if not selected or selected != list(match.events):
            raise ValueError("Match no longer corresponds to the indexed event slice; scan again")
        included = set(match.event_ids)
        return SessionBundle(session=session, events=selected,
                             omitted_event_ids=[event.id for event in events if event.id not in included])

    def _resolve_patterns(
        self,
        names: Sequence[str] | None,
    ) -> list[BehavioralPattern]:
        if names is None:
            return self._patterns
        name_set = set(names)
        active = [p for p in self._patterns if p.name in name_set]
        missing = name_set - {p.name for p in active}
        if missing:
            raise ValueError(f"Unknown pattern(s): {sorted(missing)}")
        return active
