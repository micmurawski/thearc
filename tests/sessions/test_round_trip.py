"""Exports read back by our own adapters: what each harness format keeps, and what it cannot."""

import json
from pathlib import Path

import pytest

from thearc.sessions import Event, SearchFilters, Session, Sessions, SourceReference, UnsupportedConversion

LAYOUT = {
    "claude": "project/11111111-2222-3333-4444-555555555555.jsonl",
    "codex": "rollout-x.jsonl",
    "pi": "x.jsonl",
    "opencode": "x.jsonl",
    "antigravity": "conv/.system_generated/logs/transcript.jsonl",
}


def ev(n, kind="message", role="assistant", text="", harness="pi", pointer="", **kwargs):
    return Event(
        id=f"e{n}",
        source_id="src",
        harness=harness,
        session_id="s",
        session_native_id="native-1",
        run_id="r",
        cwd="/work",
        timestamp=f"2026-10-01T10:00:{n:02d}Z",
        kind=kind,
        role=role,
        text=text,
        reference=SourceReference(
            path="/x.jsonl", generation=0, byte_offset=n, byte_length=1, line=n + 1, json_pointer=pointer
        ),
        **kwargs,
    )


SESSION = Session.from_events(
    [
        ev(0, role="user", text="Run the tests"),
        ev(1, kind="thinking", text="Check pytest first"),
        ev(2, text="Running them"),
        ev(
            3,
            kind="tool_call",
            tool_name="bash",
            call_id="c1",
            arguments={"command": "pytest"},
            text='{"command": "pytest"}',
        ),
        ev(4, kind="tool_result", role="tool", tool_name="bash", call_id="c1", status="failure", text="1 failed"),
        ev(5, text="One test fails"),
        ev(6, role="user", text="Fix it"),
        ev(7, text="Fixed"),
    ]
)


def shape(event):
    return event.kind, event.role, event.text, event.tool_name, event.status, bool(event.call_id)


def read_back(tmp_path, harness, text):
    path = tmp_path / "src" / LAYOUT[harness]
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "src", harness=harness)
        events = [e for e in sessions.index.iter_events(SearchFilters()) if e.kind != "metadata"]
    return events


@pytest.mark.parametrize("harness", ["pi", "opencode", "antigravity"])
def test_formats_with_plain_thinking_round_trip_exactly(tmp_path, harness):
    events = read_back(tmp_path, harness, SESSION.export(harness).text)
    assert [shape(e) for e in events] == [shape(e) for e in SESSION.events]
    results = [e for e in events if e.kind == "tool_result"]
    calls = {e.call_id: e.tool_name for e in events if e.kind == "tool_call"}
    assert [calls[r.call_id] for r in results] == ["bash"]


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_claude_and_codex_keep_everything_but_foreign_thinking(tmp_path, harness):
    with pytest.raises(UnsupportedConversion) if harness == "claude" else _no_error():
        SESSION.export(harness)
    events = read_back(tmp_path, harness, SESSION.export(harness, strict=False).text)
    expected = [shape(e) for e in SESSION.events]
    expected[1] = ("message", "assistant", "[Historical thinking record]\nCheck pytest first", None, None, False)
    assert [shape(e) for e in events] == expected


class _no_error:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_claude_signed_thinking_round_trips_natively(tmp_path):
    block = {"type": "thinking", "thinking": "", "signature": "c2ln"}
    session = Session.from_events(
        [
            ev(0, role="user", text="Go", harness="claude"),
            ev(
                1,
                kind="thinking",
                harness="claude",
                raw={"message": {"content": [block]}},
                pointer="/message/content/0",
            ),
        ]
    )
    events = read_back(tmp_path, "claude", session.export("claude").text)
    assert [e.kind for e in events] == ["message", "thinking"]
    assert events[1].model_dump()["raw"]["message"]["content"] == [block]


def test_claude_records_carry_what_claude_code_expects():
    records = [json.loads(line) for line in SESSION.export("claude", strict=False).text.splitlines()]
    assert {r["sessionId"] for r in records} == {records[0]["sessionId"]}
    assert records[0]["sessionId"] != "native-1"  # Not a UUID, so a stable UUID is derived from it.
    assert all(r["cwd"] == "/work" and r["isSidechain"] is False and r["version"] for r in records)
    assistant = next(r for r in records if r["type"] == "assistant")
    assert {"model", "usage", "stop_reason"} <= assistant["message"].keys()


def test_codex_header_is_synthesized_once():
    lines = [json.loads(line) for line in SESSION.export("codex", strict=False).text.splitlines()]
    assert lines[0]["type"] == "session_meta" and lines[0]["payload"]["cwd"] == "/work"
    assert sum(1 for line in lines if line["type"] == "session_meta") == 1
    failure = next(line for line in lines if line.get("payload", {}).get("type") == "function_call_output")
    assert failure["payload"]["output"][-1] == {"type": "input_text", "text": '{"exit_code": 1}'}


def test_antigravity_needs_results_in_call_order():
    session = Session.from_events(
        [
            ev(0, role="user", text="Go"),
            ev(1, kind="tool_call", tool_name="a", call_id="a", arguments={}, text="{}"),
            ev(2, kind="tool_call", tool_name="b", call_id="b", arguments={}, text="{}"),
            ev(3, kind="tool_result", role="tool", call_id="b", text="B"),
            ev(4, kind="tool_result", role="tool", call_id="a", text="A"),
        ]
    )
    with pytest.raises(UnsupportedConversion, match="by order"):
        session.export("antigravity")
    export = session.export("agy", strict=False).text
    assert export.count('"GENERIC"') == 2


def write_steps(root: Path, steps):
    path = root / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(
            json.dumps(
                {"step_index": i, "source": "MODEL", "status": "DONE", "created_at": "2026-10-01T10:00:00Z", **step}
            )
            + "\n"
            for i, step in enumerate(steps)
        )
    )


def test_antigravity_reader_pairs_typed_result_steps_with_calls(tmp_path):
    write_steps(
        tmp_path / "agy",
        [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Look around"},
            {"type": "SYSTEM_MESSAGE", "content": "Mode: planning"},
            {
                "type": "PLANNER_RESPONSE",
                "content": None,
                "tool_calls": [{"name": "view_file", "args": {"path": "a.py"}}, {"name": "run_command", "args": {}}],
            },
            {"type": "VIEW_FILE", "content": "print('a')"},
            {"type": "RUN_COMMAND", "content": "boom", "exit_code": 2},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "view_file", "args": {"path": "missing.py"}}]},
            {"type": "ERROR_MESSAGE", "error": "file not found"},
            {"type": "ERROR_MESSAGE", "error": "model overloaded"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {}}]},
            {"type": "GENERIC", "status": "RUNNING", "content": "still running"},
            {"type": "CHECKPOINT", "content": "Summary so far"},
        ],
    )
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "agy", harness="antigravity")
        events = list(sessions.index.iter_events(SearchFilters()))
    calls = {e.call_id: e.tool_name for e in events if e.kind == "tool_call"}
    results = [(calls.get(e.call_id), e.status, e.text) for e in events if e.kind == "tool_result"]
    assert results == [
        ("view_file", "success", "print('a')"),
        ("run_command", "failure", "boom"),
        ("view_file", "failure", "file not found"),
        ("run_command", None, "still running"),
    ]
    assert ("message", "system", "Mode: planning") in [(e.kind, e.role, e.text) for e in events]
    assert ("metadata", "model overloaded") in [(e.kind, e.text) for e in events]
    assert events[-1].kind == "compaction" and events[-1].text == "Summary so far"


def test_codex_reader_shows_list_outputs_as_text(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    (root / "rollout-x.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"type": "session_meta", "payload": {"id": "t"}},
                {
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "call_id": "c", "name": "exec", "input": "ls"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": "c",
                        "output": [
                            {"type": "input_text", "text": "Script failed"},
                            {"type": "input_text", "text": '{"exit_code": 1}'},
                        ],
                    },
                },
            ]
        )
    )
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(root, harness="codex")
        result = next(e for e in sessions.index.iter_events(SearchFilters()) if e.kind == "tool_result")
    assert (result.text, result.status) == ("Script failed", "failure")
