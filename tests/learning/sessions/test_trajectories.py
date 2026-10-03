"""Prompt boundaries preserve recorded activity and resolve native handoff cutoffs."""

import json

import pytest

from thearc.learning import Event, SessionStore, SourceConfig, SourceReference, split_trajectories
from thearc.learning.reflection import NativeSessionRef


def event(i, *, kind="message", role="assistant", text="response", raw=None, run="main"):
    return Event(id=f"e{i}", source_id="source", harness="codex", session_id="session", run_id=run,
                 kind=kind, role=role, text=text, raw=raw or {},
                 reference=SourceReference(path="session.jsonl", generation=0, byte_offset=i,
                                           byte_length=1, line=i + 1))


def marker(i, kind, turn):
    return event(i, kind="metadata", role=None, text="", raw={
        "type": "turn_context" if kind == "turn_context" else "event_msg",
        "payload": {"type": kind, "turn_id": turn},
    })


def test_prompt_trajectory_pairs_preserve_all_event_kinds():
    events = [event(0, role="system"), event(1, role="user", text="Fix"),
              event(2, kind="thinking"), event(3, kind="tool_call"), event(4, kind="tool_result", role="tool"),
              event(5), event(6, role="user", text="Explain"), event(7)]
    split = split_trajectories(events)
    assert split.unassigned_events == events[:1]
    first, second = split.trajectories
    assert first.prompt_text == "Fix"
    assert first.events == events[2:6]
    assert first.boundary == "next_prompt"  # No explicit completion signal.
    assert second.prompt_events == [events[6]]
    assert second.events == events[7:]
    assert second.boundary == "end_of_stream"
    assert first.id == events[1].id
    assert first.event_ids == [e.id for e in events[2:6]]


def test_one_prompt_can_have_multiple_native_content_blocks():
    first = event(0, role="user", text="First block")
    second = first.model_copy(update={"id": "e0b", "text": "Second block"})
    split = split_trajectories([first, second, event(1)])
    assert len(split.trajectories) == 1
    assert split.trajectories[0].prompt_events == [first, second]
    assert split.trajectories[0].prompt_text == "First block\nSecond block"


@pytest.mark.parametrize("ending, expected", [("task_complete", "completed"),
                                               ("task_completed", "completed"), ("turn_aborted", "interrupted")])
def test_explicit_lifecycle_and_unassigned_events(ending, expected):
    events = [marker(0, "task_started", "t1"), event(1, role="user"),
              marker(2, "turn_context", "t1"), event(3), marker(4, ending, "t1"), event(5, kind="metadata")]
    result = split_trajectories(events)
    assert result.unassigned_events == [events[0], events[-1]]
    assert result.trajectories[0].boundary == expected
    assert result.trajectories[0].native_turn_id == "t1"
    assert result.trajectories[0].events == events[2:5]


def test_separate_runs_are_not_joined_and_orphan_activity_is_retained():
    events = [event(0, role="user"), event(1), event(2, run="child"), event(3, role="user", run="child")]
    result = split_trajectories(events)
    assert result.unassigned_events == [events[2]]
    assert result.trajectories[0].boundary == "stream_boundary"
    assert result.trajectories[1].events == []
    assert split_trajectories([]).trajectories == []
    assert split_trajectories([event(0)]).unassigned_events == [event(0)]


def test_two_prompts_within_a_native_turn_cannot_claim_separate_fork_boundaries():
    result = split_trajectories([marker(0, "task_started", "t1"), event(1, role="user"), event(2),
                                 event(3, role="user"), event(4), marker(5, "task_complete", "t1")])
    assert len(result.trajectories) == 2
    assert all(t.native_turn_id is None for t in result.trajectories)


def test_sqlite_split_uses_complete_records_and_prepares_native_reference(tmp_path):
    records = [{"type": "session_meta", "payload": {"id": "native"}}]
    for turn in ("t1", "t2"):
        records.extend([
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}},
            {"type": "turn_context", "payload": {"turn_id": turn}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": turn}]}},
            {"type": "response_item", "payload": {"type": "reasoning", "summary": [{"text": "recorded"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Done"}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn}},
        ])
    (tmp_path / "rollout-native.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="source", harness="codex", root=tmp_path))
        session = next(store.iter_sessions())
        result = store.split_session(session.id)
        assert [t.prompt_text for t in result.trajectories] == ["t1", "t2"]
        assert [t.native_turn_id for t in result.trajectories] == ["t1", "t2"]
        all_events = result.unassigned_events + [e for t in result.trajectories for e in t.prompt_events + t.events]
        assert len(all_events) == len({e.id for e in all_events}) == session.event_count
        source = NativeSessionRef.from_trajectory(session, result.trajectories[0])
        assert source.session_id == "native" and source.through_turn_id == "t1"
        with pytest.raises(ValueError, match="unambiguous"):
            missing = result.trajectories[0].model_copy(update={"native_turn_id": None})
            NativeSessionRef.from_trajectory(session, missing)
        with pytest.raises(ValueError, match="main run"):
            NativeSessionRef.from_trajectory(session.model_copy(update={"id": "wrong"}), result.trajectories[0])
        with pytest.raises(KeyError):
            store.split_session("missing")


def test_native_user_markers_distinguish_environment_setup_from_prompts():
    events = [marker(0, "task_started", "t1"), event(1, role="user", text="environment setup"),
              marker(2, "turn_context", "t1"), event(3, role="user", text="actual task"),
              event(4, kind="metadata", raw={"type": "event_msg", "payload": {
                  "type": "item_completed", "turn_id": "t1",
                  "item": {"type": "UserMessage", "content": [{"type": "text", "text": "actual task"}]},
              }}), event(5), marker(6, "task_complete", "t1")]
    split = split_trajectories(events)
    assert len(split.trajectories) == 1
    assert split.trajectories[0].prompt_text == "actual task"
    assert split.trajectories[0].native_turn_id == "t1"
    assert events[1] in split.unassigned_events
