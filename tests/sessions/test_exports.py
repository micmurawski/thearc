import json

import pytest

from thearc.sessions import Event, Session, SourceReference, UnsupportedConversion


def event(number, harness="claude", *, kind="message", role="assistant", text="", raw=None, pointer="", **kwargs):
    return Event(
        id=f"e{number}",
        source_id="fixture",
        harness=harness,
        session_id="s",
        run_id="r",
        kind=kind,
        role=role,
        text=text,
        raw=raw or {},
        **kwargs,
        reference=SourceReference(
            path="/fixture.jsonl",
            generation=0,
            byte_offset=number * 100,
            byte_length=100,
            line=number + 1,
            json_pointer=pointer,
        ),
    )


def claude_lines(session, **options):
    return [json.loads(line) for line in session.export("claude", **options).text.splitlines()]


SIGNED = {"type": "thinking", "thinking": "", "signature": "c2lnbmVk"}


def test_claude_thinking_with_its_signature_is_exported_natively():
    raw = {"type": "assistant", "message": {"role": "assistant", "content": [SIGNED]}}
    session = Session.from_events(
        [event(0, role="user", text="Go"), event(1, kind="thinking", raw=raw, pointer="/message/content/0")]
    )
    export = session.export("claude")
    assert json.loads(export.text.splitlines()[1])["message"]["content"] == [SIGNED]
    assert not any(note.startswith("e1:") for note in export.transformations)


def test_redacted_claude_thinking_is_exported_natively():
    block = {"type": "redacted_thinking", "data": "opaque"}
    raw = {"message": {"content": [block]}}
    session = Session.from_events([event(0, kind="thinking", raw=raw, pointer="/message/content/0")])
    assert claude_lines(session)[0]["message"]["content"] == [block]


def test_strict_refuses_and_best_effort_labels_unsigned_thinking():
    session = Session.from_events([event(0, "pi", kind="thinking", text="Check the constructor first")])
    with pytest.raises(UnsupportedConversion, match="Thinking from pi has no Claude signature: e0"):
        session.export("claude")
    export = session.export("claude", strict=False)
    text = json.loads(export.text)["message"]["content"][0]["text"]
    assert text == "[Historical thinking record]\nCheck the constructor first"
    assert "e0: thinking from pi has no Claude signature; kept as labelled text" in export.transformations


def test_best_effort_system_messages_and_non_json_arguments():
    session = Session.from_events(
        [
            event(0, role="system", text="Be careful"),
            event(1, kind="tool_call", tool_name="shell", call_id="c", arguments="ls -la", text="ls -la"),
            event(2, kind="tool_result", role="tool", call_id="c", text="files"),
        ]
    )
    with pytest.raises(UnsupportedConversion, match="no system messages: e0"):
        session.export("claude")
    system, call, result = claude_lines(session, strict=False)
    assert system["message"] == {"role": "user", "content": [{"type": "text", "text": "[System message]\nBe careful"}]}
    assert call["message"]["content"][0]["input"] == {"raw_arguments": "ls -la"}
    assert result["message"]["content"][0]["tool_use_id"] == "c"


def test_best_effort_codex_items():
    def native(number, payload):
        return event(number, "codex", kind="metadata", role=None, raw={"type": "response_item", "payload": payload})

    session = Session.from_events(
        [
            event(0, "codex", role="user", text="Patch it"),
            native(
                1,
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Look at main.py"}],
                    "encrypted_content": "gAAA",
                },
            ),
            native(2, {"type": "custom_tool_call", "call_id": "p", "name": "apply_patch", "input": "*** Begin Patch"}),
            native(3, {"type": "custom_tool_call_output", "call_id": "p", "output": "Done"}),
        ]
    )
    with pytest.raises(UnsupportedConversion, match="encrypted reasoning cannot be exported to Claude transcripts: e1"):
        session.export("claude")
    export = session.export("claude", strict=False)
    _, reasoning, call, result = (json.loads(line) for line in export.text.splitlines())
    assert reasoning["message"]["content"][0]["text"] == "[Codex reasoning summary]\nLook at main.py"
    assert call["message"]["content"][0] == {
        "type": "tool_use",
        "id": "p",
        "name": "apply_patch",
        "input": {"input": "*** Begin Patch"},
    }
    assert result["message"]["content"][0]["content"] == "Done"
    assert [note.split(":")[0] for note in export.transformations if note.startswith("e")] == ["e1", "e2"]


def test_best_effort_orphan_tool_result_and_unknown_kinds():
    session = Session.from_events(
        [
            event(0, kind="tool_result", role="tool", call_id="missing", text="output"),
            event(1, kind="unknown", text="?", raw={"x": 1}, pointer="/x"),
        ]
    )
    with pytest.raises(UnsupportedConversion, match="no preceding call"):
        session.export("codex")
    lines = session.export("codex", strict=False).text.splitlines()[1:]  # After the session_meta header.
    orphan, unknown = (json.loads(line)["payload"] for line in lines)
    assert orphan["content"][0]["text"] == "[Tool result without its call]\noutput"
    assert unknown["content"][0]["text"] == "[unknown record]\n?"


def test_cli_best_effort_lists_transformations_on_stderr(tmp_path):
    from click.testing import CliRunner

    from thearc.cli import main

    from .test_sessions import write_jsonl

    write_jsonl(
        tmp_path / "pi" / "s.jsonl",
        [
            {"type": "session", "id": "native", "timestamp": "2026-10-01T10:00:00Z"},
            {"type": "message", "message": {"role": "user", "content": "Go"}},
            {"type": "message", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "Hmm"}]}},
        ],
    )
    runner = CliRunner()
    index = ["sessions", "--index", str(tmp_path / "index.sqlite")]
    assert runner.invoke(main, [*index, "add", str(tmp_path / "pi")]).exit_code == 0
    strict = runner.invoke(main, [*index, "render", "--format", "claude"])
    assert strict.exit_code != 0 and "Use --best-effort" in strict.output
    best = runner.invoke(main, [*index, "render", "--format", "claude", "--best-effort"])
    assert best.exit_code == 0, best.output
    assert "[Historical thinking record]\\nHmm" in best.stdout
    assert "note: " in best.stderr and "has no Claude signature" in best.stderr


def test_a_codex_copy_is_a_self_contained_rollout():
    """Newer Codex sessions keep paginated history in Codex's own store; a copy has only the file."""

    def native(number, record):
        return event(number, "codex", kind="metadata", role=None, raw=record)

    original = "11111111-1111-4111-8111-111111111111"
    session = Session.from_events([
        native(0, {"type": "session_meta", "payload": {"id": original, "session_id": original,
                                                         "history_mode": "paginated", "cwd": "/w", "source": "exec"}}),
        native(1, {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}}),
        event(2, "codex", role="user", text="Patch it"),
        event(3, "codex", text="Done"),
        native(4, {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}}),
    ])
    export = session.export("codex", strict=False)
    header = json.loads(export.text.splitlines()[0])
    assert header["type"] == "session_meta" and "history_mode" not in header["payload"]
    assert header["payload"]["id"] == header["payload"]["session_id"] != original
    assert any("history_mode dropped" in note for note in export.transformations)


def test_codex_reasoning_keeps_the_id_its_encrypted_content_is_bound_to():
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAA"}
    call = {"type": "custom_tool_call", "id": "ctc_1", "call_id": "p", "name": "apply_patch", "input": "x"}
    session = Session.from_events([
        event(0, "codex", role="user", text="Patch it"),
        event(1, "codex", kind="metadata", role=None, raw={"type": "response_item", "payload": reasoning}),
        event(2, "codex", kind="metadata", role=None, raw={"type": "response_item", "payload": call}),
    ])
    items = [json.loads(line)["payload"] for line in session.export("codex").text.splitlines()
             if json.loads(line).get("type") == "response_item"]
    assert next(i for i in items if i["type"] == "reasoning")["id"] == "rs_1"  # The API checks the pairing.
    assert "id" not in next(i for i in items if i["type"] == "custom_tool_call")
