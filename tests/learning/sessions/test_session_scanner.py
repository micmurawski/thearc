"""Tests for thearc.learning.sessions.scanner module.

Run with:
    python -m pytest tests/learning/sessions/test_session_scanner.py -v
"""

from __future__ import annotations

from typing import Any

import pytest

from thearc.learning.sessions.models import Event, SourceReference
from thearc.learning.sessions.scanner import (
    DEFAULT_PATTERNS,
    AbandonedFix,
    BehavioralPattern,
    EmptyToolResult,
    FrustrationSpike,
    IgnoredUserInstruction,
    LongSilence,
    PatternMatch,
    PatternScanner,
    SelfOverwrite,
    StubbornToolLoop,
)

# ---------------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------------

_REF = SourceReference(path="fake.jsonl", generation=1, byte_offset=0, byte_length=0, line=1)
_COUNTER = 0


def _evt(
    *,
    kind: str = "message",
    role: str | None = None,
    text: str = "",
    tool_name: str | None = None,
    action_kind: str | None = None,
    status: str | None = None,
    arguments: Any = None,
    session_id: str = "sess-1",
) -> Event:
    global _COUNTER
    _COUNTER += 1
    return Event(
        id=f"evt-{_COUNTER:04d}",
        source_id="test",
        harness="codex",
        session_id=session_id,
        run_id="run-1",
        kind=kind,
        role=role,
        text=text,
        tool_name=tool_name,
        action_kind=action_kind,
        status=status,
        arguments=arguments,
        reference=_REF,
    )


def _tool_call(tool: str = "run_command", args: Any = None, **kw) -> Event:
    return _evt(kind="tool_call", role="assistant", tool_name=tool, action_kind=tool, arguments=args, **kw)


def _tool_result(tool: str = "run_command", status: str = "failure", text: str = "Error!", **kw) -> Event:
    return _evt(kind="tool_result", tool_name=tool, action_kind=tool, status=status, text=text, **kw)


def _user(text: str = "Fix the bug") -> Event:
    return _evt(kind="message", role="user", text=text)


def _assistant(text: str = "Sure, looking into it.") -> Event:
    return _evt(kind="message", role="assistant", text=text)


def _write_call(file: str = "/src/main.py") -> Event:
    return _tool_call("write_to_file", args={"TargetFile": file})


def _write_result(status: str = "success", text: str = "Done") -> Event:
    return _tool_result("write_to_file", status=status, text=text)


# ---------------------------------------------------------------------------
# StubbornToolLoop
# ---------------------------------------------------------------------------


class TestStubbornToolLoop:
    def test_basic_loop(self):
        events = [
            _tool_call("run_command"),
            _tool_result("run_command", "failure"),
            _tool_call("run_command"),
            _tool_result("run_command", "failure"),
        ]
        matches = StubbornToolLoop(min_repeats=2).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["tool"] == "run_command"
        assert matches[0].context["cycles"] == 2
        assert matches[0].severity == "warning"

    def test_three_repeats(self):
        events = [
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
        ]
        matches = StubbornToolLoop(min_repeats=3).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["cycles"] == 3

    def test_different_tools_no_match(self):
        events = [
            _tool_call("run_command"),
            _tool_result("run_command", "failure"),
            _tool_call("write_to_file"),
            _tool_result("write_to_file", "failure"),
        ]
        matches = StubbornToolLoop().find_matches(events)
        assert len(matches) == 0

    def test_retry_still_matches_if_it_later_succeeds(self):
        events = [
            _tool_call("run_command"),
            _tool_result("run_command", "failure"),
            _tool_call("run_command"),
            _tool_result("run_command", status="success", text="OK"),
        ]
        matches = StubbornToolLoop().find_matches(events)
        assert len(matches) == 1
        assert matches[0].events == tuple(events[:3])

    def test_no_tool_events(self):
        events = [_user(), _assistant(), _user()]
        assert StubbornToolLoop().find_matches(events) == []


# ---------------------------------------------------------------------------
# FrustrationSpike
# ---------------------------------------------------------------------------


class TestFrustrationSpike:
    def test_basic_spike(self):
        events = [
            _tool_result("a", "failure"),
            _tool_result("b", "failure"),
            _tool_result("c", "failure"),
            _tool_call("d"),
            _assistant(),
        ]
        matches = FrustrationSpike(window=5, threshold=3).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["error_count"] == 3

    def test_below_threshold(self):
        events = [
            _tool_result("a", "failure"),
            _tool_result("b", "failure"),
            _assistant(),
            _tool_call("c"),
            _assistant(),
        ]
        matches = FrustrationSpike(window=5, threshold=3).find_matches(events)
        assert len(matches) == 0

    def test_multiple_windows(self):
        events = [
            _tool_result("a", "failure"),
            _tool_result("b", "failure"),
            _tool_result("c", "failure"),
            _assistant(),
            _assistant(),
            _tool_result("d", "failure"),
            _tool_result("e", "failure"),
            _tool_result("f", "failure"),
            _assistant(),
            _assistant(),
        ]
        matches = FrustrationSpike(window=5, threshold=3).find_matches(events)
        assert len(matches) >= 2

    def test_empty_events(self):
        assert FrustrationSpike().find_matches([]) == []


# ---------------------------------------------------------------------------
# SelfOverwrite
# ---------------------------------------------------------------------------


class TestSelfOverwrite:
    def test_basic_double_write(self):
        events = [
            _write_call("/src/app.py"),
            _write_result(),
            _write_call("/src/app.py"),
            _write_result(),
        ]
        matches = SelfOverwrite(min_writes=2).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["file"] == "/src/app.py"
        assert matches[0].context["write_count"] == 2

    def test_different_files_no_match(self):
        events = [
            _write_call("/src/app.py"),
            _write_result(),
            _write_call("/src/utils.py"),
            _write_result(),
        ]
        matches = SelfOverwrite(min_writes=2).find_matches(events)
        assert len(matches) == 0

    def test_three_writes(self):
        events = [
            _write_call("/x.py"),
            _write_result(),
            _write_call("/x.py"),
            _write_result(),
            _write_call("/x.py"),
            _write_result(),
        ]
        matches = SelfOverwrite(min_writes=2).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["write_count"] == 3


# ---------------------------------------------------------------------------
# IgnoredUserInstruction
# ---------------------------------------------------------------------------


class TestIgnoredUserInstruction:
    def test_basic_ignored(self):
        events = [
            _user("Please fix the test"),
            _tool_call("run_command"),
            _tool_result("run_command", "success", "OK"),
        ]
        matches = IgnoredUserInstruction().find_matches(events)
        assert len(matches) == 1
        assert "user_text_preview" in matches[0].context

    def test_acknowledged(self):
        events = [
            _user("Please fix the test"),
            _assistant("On it, running the tests now."),
            _tool_call("run_command"),
        ]
        matches = IgnoredUserInstruction().find_matches(events)
        assert len(matches) == 0

    def test_no_tool_call(self):
        events = [
            _user("Just a question?"),
            _assistant("Here is the answer."),
        ]
        matches = IgnoredUserInstruction().find_matches(events)
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# EmptyToolResult
# ---------------------------------------------------------------------------


class TestEmptyToolResult:
    def test_basic_empty(self):
        events = [
            _tool_call("run_command"),
            _tool_result("run_command", "success", text=""),
        ]
        matches = EmptyToolResult().find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["tool"] == "run_command"

    def test_non_empty(self):
        events = [
            _tool_call("run_command"),
            _tool_result("run_command", "success", text="All tests passed"),
        ]
        matches = EmptyToolResult().find_matches(events)
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# AbandonedFix
# ---------------------------------------------------------------------------


class TestAbandonedFix:
    def test_basic_abandon(self):
        events = [
            _write_call("/src/main.py"),
            _write_result(status="failure", text="SyntaxError"),
            _write_call("/src/other.py"),
            _write_result(),
        ]
        matches = AbandonedFix().find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["file"] == "/src/main.py"

    def test_came_back(self):
        events = [
            _write_call("/src/main.py"),
            _write_result(status="failure", text="SyntaxError"),
            _tool_call("run_command"),
            _tool_result("run_command", "success", "OK"),
            _write_call("/src/main.py"),
            _write_result(),
        ]
        matches = AbandonedFix().find_matches(events)
        assert len(matches) == 0

    def test_success_no_abandon(self):
        events = [
            _write_call("/src/main.py"),
            _write_result(status="success"),
        ]
        matches = AbandonedFix().find_matches(events)
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# LongSilence
# ---------------------------------------------------------------------------


class TestLongSilence:
    def test_basic_silence(self):
        events = [_assistant("Starting")] + [_tool_call() for _ in range(10)] + [_assistant("Done")]
        matches = LongSilence(threshold=8).find_matches(events)
        assert len(matches) == 1
        assert matches[0].context["silent_events"] == 10

    def test_below_threshold(self):
        events = [_assistant("Hi")] + [_tool_call() for _ in range(3)] + [_assistant("Done")]
        matches = LongSilence(threshold=8).find_matches(events)
        assert len(matches) == 0

    def test_trailing_silence(self):
        events = [_assistant("Starting")] + [_tool_call() for _ in range(10)]
        matches = LongSilence(threshold=8).find_matches(events)
        assert len(matches) == 1
        assert "end of session" in matches[0].description


# ---------------------------------------------------------------------------
# PatternScanner
# ---------------------------------------------------------------------------


class TestPatternScanner:
    def test_scan_events_direct(self):
        events = [
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
        ]
        scanner = PatternScanner(store=None, patterns=[StubbornToolLoop()])
        matches = scanner.scan_events(events)
        assert len(matches) == 1
        assert matches[0].pattern_name == "stubborn_tool_loop"

    def test_pattern_name_filter(self):
        events = [
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
        ]
        scanner = PatternScanner(store=None, patterns=DEFAULT_PATTERNS)
        # Only check frustration spike — should find nothing
        matches = scanner.scan_events(events, pattern_names=["frustration_spike"])
        stubborn_matches = [m for m in matches if m.pattern_name == "stubborn_tool_loop"]
        assert len(stubborn_matches) == 0

    def test_unknown_pattern_name_raises(self):
        scanner = PatternScanner(store=None, patterns=DEFAULT_PATTERNS)
        with pytest.raises(ValueError, match="Unknown pattern"):
            scanner.scan_events([], pattern_names=["nonexistent_pattern"])

    def test_summary(self):
        events = [
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_result("x", "failure", text=""),  # empty + error
        ]
        scanner = PatternScanner(store=None, patterns=DEFAULT_PATTERNS)
        matches = scanner.scan_events(events)
        summary = scanner.summary(matches)
        assert summary["total_matches"] > 0
        assert "sess-1" in summary["sessions_affected"]
        assert isinstance(summary["by_pattern"], dict)
        assert isinstance(summary["by_severity"], dict)

    def test_empty_events(self):
        scanner = PatternScanner(store=None, patterns=DEFAULT_PATTERNS)
        matches = scanner.scan_events([])
        assert matches == []

    def test_patternmatch_properties(self):
        events = [
            _tool_call("bash"),
            _tool_result("bash", "failure"),
            _tool_call("bash"),
            _tool_result("bash", "failure"),
        ]
        scanner = PatternScanner(store=None, patterns=[StubbornToolLoop()])
        matches = scanner.scan_events(events)
        m = matches[0]
        assert len(m.event_ids) == len(m.events)
        assert m.span == m.end_index - m.start_index + 1
        assert m.span >= 2


# SQLite candidate filtering and exact sequence/coverage contracts.

@pytest.fixture
def indexed_store(tmp_path):
    import json

    from thearc.learning import SessionStore, SourceConfig

    root = tmp_path / "logs"
    root.mkdir()
    for name, tool, failure in [("retry", "bash", True), ("other", "read", True), ("ok", "bash", False)]:
        records = [
            {"role": "user", "content": "Investigate"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": tool,
                                                 "input": {"command": "test"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1",
                                            "content": "Error" if failure else "OK", "is_error": failure}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-2", "name": tool,
                                                 "input": {"command": "test again"}}]},
            {"role": "assistant", "content": "Finished"},
        ]
        (root / f"{name}.jsonl").write_text("\n".join(json.dumps({
            "sessionId": name, "timestamp": "2026-09-01T00:00:00Z", "message": message,
        }) for message in records) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="test", harness="claude", root=root))
        yield store


def test_sql_filters_select_sessions_and_load_complete_sequences(indexed_store, monkeypatch):
    from thearc.learning import SearchFilters

    store = indexed_store
    filters = SearchFilters(statuses=["failure"], tool_names=["bash"], roles=["tool"],
                            started_after="2026-08-01T00:00:00Z", source_ids=["test"])
    decoded = []
    original = store._session_from_row

    def decode(row):
        decoded.append(row["native_id"])
        return original(row)

    monkeypatch.setattr(store, "_session_from_row", decode)
    observed = []

    class Observe(BehavioralPattern):
        name = "observe"

        def find_matches(self, events):
            observed.append(events)
            return []

    scanner = PatternScanner(store, patterns=[StubbornToolLoop(), Observe()])
    matches = scanner.scan(filters=filters)
    assert decoded == ["retry"]  # SQL rejects rows before any Python session decoding.
    assert len(observed) == 1
    events = observed[0]
    assert len(events) == 5  # status/role filters did not strip neighboring events.
    assert len(matches) == 1
    match = matches[0]
    assert match.start_index == 1
    assert match.end_index == 3
    assert match.events == tuple(events[1:4])
    assert match.events[1].tool_name == "bash"  # Result enriched through call_id.
    assert scanner.scan_session(match.session_id) == matches
    assert scanner.scan(statuses=["failure"], tool_names=["bash"]) == matches
    with pytest.raises(ValueError, match="either filters"):
        scanner.scan(filters=filters, statuses=["failure"])
    with pytest.raises(ValueError, match="Unknown filter"):
        scanner.scan(status=["failure"])
    assert scanner.scan(filters=SearchFilters(source_ids=["missing"])) == []
    assert scanner.scan(filters=SearchFilters(harnesses=["codex"])) == []
    assert scanner.scan(filters=SearchFilters(run_ids=[events[0].run_id])) == matches
    assert scanner.scan(pattern_names=[]) == []


def test_filters_must_match_the_same_event(indexed_store):
    from thearc.learning import SearchFilters

    # These sessions contain both calls and failures, but no failed call event.
    assert list(indexed_store.iter_candidate_sessions(SearchFilters(
        statuses=["failure"], kinds=["tool_call"],
    ))) == []


def test_reflection_bundle_preserves_slice_and_reports_omissions(indexed_store):
    from dataclasses import replace

    scanner = PatternScanner(indexed_store)
    match = scanner.scan()[0]
    bundle = scanner.reflection_bundle(match)
    assert bundle.events == list(match.events)
    assert bundle.session.id == match.session_id
    assert len(bundle.omitted_event_ids) == 2
    assert not set(bundle.omitted_event_ids).intersection(match.event_ids)
    with pytest.raises(ValueError, match="scan again"):
        scanner.reflection_bundle(replace(match, start_index=0))


def test_candidate_selection_has_no_page_cap(tmp_path):
    import json

    from thearc.learning import SearchFilters, SessionStore, SourceConfig

    root = tmp_path / "logs"
    root.mkdir()
    # One artifact can contain many sessions. All should reach Python matching.
    records = [{"sessionId": f"s-{i}", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c", "is_error": True, "content": ""},
    ]}} for i in range(1005)]
    (root / "sessions.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="test", harness="claude", root=root))
        matches = PatternScanner(store, patterns=[EmptyToolResult()]).scan(filters=SearchFilters(statuses=["failure"]))
    assert len(matches) == 1005
    assert len({match.session_id for match in matches}) == 1005


@pytest.mark.parametrize("status", ["failure", "error"])
def test_retry_exact_three_events_and_call_correlation(status):
    call = _tool_call("bash").model_copy(update={"call_id": "c1"})
    result = _tool_result("bash", status).model_copy(update={"call_id": "c1"})
    retry = _tool_call("bash").model_copy(update={"call_id": "c2"})
    pattern = StubbornToolLoop()
    assert pattern.find_matches([call, result, retry])[0].events == (call, result, retry)
    assert pattern.find_matches([call, result.model_copy(update={"call_id": "wrong"}), retry]) == []
    assert pattern.find_matches([call, result.model_copy(update={"kind": "message"}), retry]) == []
    assert pattern.find_matches([call, result, _assistant(), retry]) == []


@pytest.mark.parametrize("change", ["session_id", "source_id", "run_id", "path", "generation"])
def test_no_match_crosses_conversation_or_artifact_boundaries(change):
    events = [_tool_call(), _tool_result(), _tool_call()]
    if change in {"path", "generation"}:
        value = "other.jsonl" if change == "path" else 2
        events[-1] = events[-1].model_copy(update={
            "reference": events[-1].reference.model_copy(update={change: value}),
        })
    else:
        events[-1] = events[-1].model_copy(update={change: "other"})
    assert StubbornToolLoop().find_matches(events) == []
    assert PatternScanner().scan_events(events) == []
    failed = [e.model_copy(update={"status": "failure"}) for e in events]
    assert FrustrationSpike(window=3, threshold=3).find_matches(failed) == []


def test_custom_patterns_receive_contiguous_segments_and_original_offsets():
    class AllEvents(BehavioralPattern):
        name = "all_events"
        description = "Record each native stream"

        def find_matches(self, events):
            return [PatternMatch(pattern_name=self.name, description=self.description,
                                 session_id=events[0].session_id, start_index=0, end_index=len(events) - 1,
                                 events=tuple(events))]

    first = [_assistant()]
    second = [_evt(session_id="other"), _evt(session_id="other")]
    matches = PatternScanner(patterns=[AllEvents()]).scan_events(first + second)
    assert [match.start_index for match in matches] == [0, 1]
    assert [match.end_index for match in matches] == [0, 2]
    assert matches[1].events == tuple(second)


def test_overlapping_windows_are_preserved():
    errors = [_tool_result() for _ in range(6)]
    assert [m.start_index for m in FrustrationSpike().find_matches(errors)] == [0, 1]
    events = [_tool_call(), _tool_result(), _tool_call(), _tool_result(), _tool_call()]
    assert [m.start_index for m in StubbornToolLoop().find_matches(events)] == [0, 2]


@pytest.mark.parametrize("factory, kwargs", [
    (StubbornToolLoop, {"min_repeats": 1}),
    (StubbornToolLoop, {"min_repeats": 2.5}),
    (FrustrationSpike, {"window": 0}),
    (FrustrationSpike, {"threshold": -1}),
    (FrustrationSpike, {"threshold": 6}),
    (FrustrationSpike, {"window": True}),
])
def test_invalid_pattern_parameters(factory, kwargs):
    with pytest.raises(ValueError):
        factory(**kwargs)


def test_default_patterns_are_independent_and_store_is_required_for_queries():
    scanner = PatternScanner()
    assert [p.name for p in scanner.patterns] == ["stubborn_tool_loop", "frustration_spike"]
    scanner.patterns[0].min_repeats = 10
    assert PatternScanner().patterns[0].min_repeats == 2
    for query in (scanner.scan, lambda: scanner.scan_session("missing")):
        with pytest.raises(ValueError, match="SessionStore"):
            query()


def test_abandoned_fix_requires_evidence_of_editing_another_file():
    initial = [_write_call("/src/main.py"), _write_result(status="failure")]
    pattern = AbandonedFix()
    assert pattern.find_matches([*initial, _assistant("Investigating")]) == []
    assert pattern.find_matches([*initial, _tool_call("read_file", args={"path": "/src/other.py"})]) == []
    events = [*initial, _assistant("Investigating"), _write_call("/src/other.py")]
    match = pattern.find_matches(events)[0]
    assert match.events == tuple(events)
    assert match.end_index == 3


def test_pattern_implementations_remain_available_from_scanner():
    from thearc.learning.sessions import patterns, scanner

    for name in ("PatternMatch", "BehavioralPattern", "StubbornToolLoop", "FrustrationSpike", "AbandonedFix"):
        assert getattr(scanner, name) is getattr(patterns, name)
