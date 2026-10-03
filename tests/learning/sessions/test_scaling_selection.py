"""Selection correctness before artifact-reference and bounded-memory work."""

import pytest

from thearc import MetaAgent
from thearc.learning import (
    AceConfig,
    AcePipeline,
    AceQuery,
    Event,
    HistorySessionMaterializer,
    HistorySessionSelector,
    Reflection,
    SearchFilters,
    SessionBundle,
    SessionStore,
    SourceReference,
)
from thearc.learning.reflection.flow import build_reflection_flow


@pytest.fixture
def many_sessions(tmp_path):
    with SessionStore(tmp_path / "sessions.sqlite") as store:
        # Projection-only fixture: avoids thousands of transcript files while
        # exercising the real SQLite enumeration and pipeline selection paths.
        with store.connection:
            store.connection.executemany(
                "INSERT INTO sessions (id,source_id,harness,native_id,ended_at,event_count,run_count,metadata) "
                "VALUES (?,?,?,?,?,0,0,'{}')",
                [(f"s{i:05d}", "main" if i < 1005 else "other", "pi" if i % 2 else "claude",
                  f"native-{i}", None if i % 3 else "2026-01-01T00:00:00Z") for i in range(1010)],
            )
        yield store


def test_session_iteration_complete_filtered_and_same_order(many_sessions):
    store = many_sessions
    all_sessions = list(store.iter_sessions())
    assert len(all_sessions) == len({session.id for session in all_sessions}) == 1010
    paged = store.list_sessions(limit=1000) + store.list_sessions(limit=1000, offset=1000)
    assert all_sessions == paged
    filters = SearchFilters(source_ids=["main"], harnesses=["pi"], session_ids=["s00001", "s00002", "s01009"])
    assert [s.id for s in store.iter_sessions(filters)] == ["s00001"]
    assert list(store.iter_sessions(SearchFilters(source_ids=["absent"]))) == []
    assert len(HistorySessionSelector(store).select(AceQuery(filters=SearchFilters(source_ids=["main"])))) == 1005


def test_session_iterator_does_not_decode_entire_selection(many_sessions, monkeypatch):
    seen = []
    original = many_sessions._session_from_row

    def decode(row):
        seen.append(row["id"])
        return original(row)

    monkeypatch.setattr(many_sessions, "_session_from_row", decode)
    iterator = many_sessions.iter_sessions()
    assert not seen
    assert next(iterator).id == seen[0]
    assert len(seen) == 1
    iterator.close()


def test_standard_ace_and_reflection_flow_do_not_cap_at_1000(many_sessions):
    class Materializer:
        def materialize(self, session, config):
            return SessionBundle(session=session)

    class Reflector:
        def reflect(self, batch, agent):
            return Reflection(id=f"reflection-{batch[0].session.id}", session_ids=[b.session.id for b in batch])

    result = AcePipeline(HistorySessionSelector(many_sessions), Materializer(), Reflector(),
                         config=AceConfig(sessions_per_reflection=256)).run(
        MetaAgent(name="test"), reflection_only=True,
    )
    assert len(result.sessions) == 1010
    assert [len(r.session_ids) for r in result.reflections] == [256, 256, 256, 242]
    # Isolate the standalone flow's selection node without inference/materialization.
    shared = {"history": many_sessions, "source_ids": ["main"]}
    build_reflection_flow().start_node._run(shared)
    assert len(shared["sessions"]) == 1005


@pytest.mark.parametrize("timestamps", [
    [None] * 6,
    ["2026-01-01T00:00:00Z"] * 6,
    [f"2026-01-01T00:00:0{i}Z" for i in reversed(range(6))],
])
def test_native_event_order_and_task_outcome_selection(tmp_path, timestamps):
    with SessionStore(tmp_path / "events.sqlite") as store:
        events = []
        for i, (identity, role, text, offset, subrecord) in enumerate([
            ("z", "user", "Original task", 0, 0),
            ("y", "assistant", "Investigating", 100, 0),
            ("x", "assistant", "Tool call", 100, 1),
            ("w", "tool", "Tool result", 200, 0),
            ("v", "user", "Follow-up", 300, 0),
            ("a", "assistant", "Final answer", 400, 0),
        ]):
            event = Event(id=identity, source_id="source", harness="pi", session_id="s", run_id="run",
                          session_native_id="native", role=role, text=text, timestamp=timestamps[i],
                          kind="tool_call" if i == 2 else "tool_result" if i == 3 else "message",
                          call_id="call" if i in (2, 3) else None,
                          reference=SourceReference(path=tmp_path / "session.jsonl", generation=0,
                                                    byte_offset=offset, byte_length=100, line=i + 1))
            events.append((event, subrecord))
        with store.connection:
            for event, subrecord in reversed(events):
                store._insert(event, subrecord)
        store._refresh_projections()
        assert [e.id for e in store.iter_events()] == ["z", "y", "x", "w", "v", "a"]
        assert [e.id for e in store.iter_events(SearchFilters(roles=["assistant"]))] == ["y", "x", "a"]
        bundle = HistorySessionMaterializer(store).materialize(
            store.get_session("s"), AceConfig(max_events_per_session=2),
        )
        assert [e.text for e in bundle.events] == ["Original task", "Final answer"]
        assert set(bundle.omitted_event_ids) == {"y", "x", "w", "v"}
        larger = HistorySessionMaterializer(store).materialize(
            store.get_session("s"), AceConfig(max_events_per_session=5),
        )
        assert [e.id for e in larger.events] == ["z", "y", "x", "w", "a"]


def test_artifact_order_is_deterministic_and_native_offsets_do_not_interleave(tmp_path):
    with SessionStore(tmp_path / "events.sqlite") as store:
        with store.connection:
            for path in ("b.jsonl", "a.jsonl"):
                for offset in (100, 0):
                    store._insert(Event(
                        id=f"{path}-{offset}", source_id="source", harness="pi", session_id="s", run_id="run",
                        kind="message", role="assistant", text=path,
                        reference=SourceReference(path=tmp_path / path, generation=0, byte_offset=offset,
                                                  byte_length=100, line=offset // 100 + 1),
                    ), 0)
        expected = ["a.jsonl-0", "a.jsonl-100", "b.jsonl-0", "b.jsonl-100"]
        assert [e.id for e in store.iter_events()] == expected
        assert [e.id for e in store.iter_events()] == expected
