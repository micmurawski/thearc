import json

from thearc.sessions.index import SessionIndex
from thearc.sessions.models import SearchFilters, SourceConfig
from thearc.sessions.trajectories import split_trajectories


def test_export_message_boundaries_and_original_evidence(tmp_path):
    messages = [
        {
            "info": {"role": "user"},
            "parts": [{"type": "text", "text": "First"}, {"type": "text", "text": "extra detail"}],
        },
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "Second"}]},
        {
            "info": {"role": "assistant", "time": {"created": 1000}},
            "parts": [
                {"type": "reasoning", "text": "Consider the second prompt"},
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call",
                    "state": {"status": "completed", "input": {"command": "pwd"}, "output": "/project"},
                },
                {"type": "text", "text": "Done"},
            ],
        },
    ]
    data = {"info": {"id": "session", "directory": "/project"}, "messages": messages}
    (tmp_path / "session.jsonl").write_text(json.dumps(data) + "\n")
    with SessionIndex(tmp_path / "index.sqlite") as store:
        report = store.ingest(SourceConfig(id="test", harness="opencode", root=tmp_path))
        assert report.records_skipped == 0
        events = list(store.iter_events(SearchFilters()))
        for event in events:
            assert store.read_evidence(event.id) == event.raw
        split = split_trajectories(events)
    assert len(split.trajectories) == 2
    first, second = split.trajectories
    assert first.prompt_text == "First\nextra detail"
    assert first.events == ()
    assert second.prompt_text == "Second"
    assert [e.kind for e in second.events] == ["thinking", "tool_call", "tool_result", "message"]
    assert second.events[2].status == "success"
    assert second.events[-1].timestamp == "1970-01-01T00:00:01+00:00"


def test_unsupported_export_reports_diagnostic(tmp_path):
    (tmp_path / "session.jsonl").write_text(json.dumps({"info": {"id": "bad"}, "messages": [None]}) + "\n")
    with SessionIndex(tmp_path / "index.sqlite") as store:
        report = store.ingest(SourceConfig(id="test", harness="opencode", root=tmp_path))
        assert report.records_skipped == 1
        assert list(store.iter_events(SearchFilters())) == []


def test_v2_export_preserves_prompts_tool_results_and_references(tmp_path):
    data = {
        "info": {"id": "session-v2", "location": {"directory": "/project"}},
        "messages": [
            {"type": "user", "id": "m1", "text": "First"},
            {"type": "user", "id": "m2", "text": "Second"},
            {
                "type": "assistant",
                "time": {"created": 1000, "completed": 2000},
                "finish": "stop",
                "content": [
                    {"type": "reasoning", "text": "Check the directory"},
                    {
                        "type": "tool",
                        "id": "call-1",
                        "name": "shell",
                        "state": {
                            "status": "completed",
                            "input": {"command": "pwd"},
                            "content": [{"type": "text", "text": "/project"}],
                            "metadata": {"exit": 0},
                        },
                    },
                    {
                        "type": "tool",
                        "id": "call-2",
                        "name": "shell",
                        "state": {
                            "status": "completed",
                            "input": {"command": "false"},
                            "content": [{"type": "text", "text": "Exited 1"}],
                            "metadata": {"exit": 1},
                        },
                    },
                    {"type": "text", "text": "Done"},
                ],
            },
            {"type": "idle", "outcome": "succeeded"},
        ],
    }
    (tmp_path / "session.jsonl").write_text(json.dumps(data) + "\n")
    with SessionIndex(tmp_path / "index.sqlite") as store:
        report = store.ingest(SourceConfig(id="test", harness="opencode", root=tmp_path))
        assert report.records_skipped == 0
        events = list(store.iter_events(SearchFilters()))
        assert all(e.cwd == "/project" for e in events)
        for event in events:
            assert store.read_evidence(event.id) == event.raw
        split = split_trajectories(events)
        assert [t.prompt_text for t in split.trajectories] == ["First", "Second"]
        results = [e for e in events if e.kind == "tool_result"]
        assert [(e.call_id, e.text, e.status) for e in results] == [
            ("call-1", "/project", "success"),
            ("call-2", "Exited 1", "failure"),
        ]
