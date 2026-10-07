import json
from dataclasses import FrozenInstanceError

import pytest

from thearc.learning.sessions.models import SourceConfig
from thearc.resources import Session, SessionStore, UnsupportedConversion, evidence_to_markdown

from .conftest import event


def test_immutable_detached_history_and_round_trip(history, tmp_path):
    trajectories = history.split_into_trajectories()
    assert len(trajectories) == 2
    assert trajectories[0].prompt_text == "Run tests"
    assert [e.id for e in trajectories[0].events] == ["e1", "e2", "e3"]
    assert [e.id for e in history.through(trajectories[1]).events] == [f"e{i}" for i in range(6)]
    with pytest.raises(FrozenInstanceError):
        history.artifact_id = "changed"
    with pytest.raises(TypeError):
        history.events[1].arguments["command"] = []
    with pytest.raises(AttributeError):
        history.events[0].text = "changed"
    detached = history.events[1].model_dump()
    detached["arguments"]["command"].append("bad")
    assert len(history.events[1].arguments["command"]) == 2
    path = history.save(tmp_path / "session.json")
    assert Session.open(path).to_jsonl() == history.to_jsonl()
    with pytest.raises(FileExistsError):
        history.save(path)
    data = json.loads(path.read_text())
    data["events"][0]["text"] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="checksum"):
        Session.open(path)


def test_foreign_prefix_and_stream_boundary(history):
    other = Session.from_events([event(10, role="user", text="other")])
    with pytest.raises(ValueError, match="different session artifact"):
        history.through(other.split_into_trajectories()[0])
    events = [e.model_dump() for e in history.events]
    events[-1]["run_id"] = "child"
    multi = Session.from_events(events)
    with pytest.raises(ValueError, match="Ambiguous"):
        multi.through(multi.split_into_trajectories()[0])


def test_rendering_and_conversion(history, tmp_path):
    assert evidence_to_markdown(history.events) == evidence_to_markdown(
        json.dumps([e.model_dump() for e in history.events]))
    text = history.to_markdown()
    assert "fixture.jsonl" not in text and "harness" not in text
    assert 'Responds to: "e1"' in text
    assert "SyntaxError" in text
    assert json.loads(history.to_codex().text.splitlines()[1])["payload"]["type"] == "function_call"
    assert json.loads(history.to_claude().text.splitlines()[2])["message"]["content"][0]["is_error"]
    path = history.to_codex().save(tmp_path / "export.jsonl")
    assert path.is_file()
    with pytest.raises(FileExistsError):
        history.to_codex().save(path)
    unsupported = Session.from_events([event(0, kind="unknown", text="unsupported")])
    with pytest.raises(UnsupportedConversion):
        unsupported.to_codex()


def test_metadata_is_skipped_and_reported_not_replayed(history):
    events = [e.model_dump() for e in history.events]
    attachment = event(99, kind="metadata", text="").model_dump()
    attachment["session_id"] = events[0]["session_id"]
    mixed = Session.from_events([events[0], attachment, *events[1:]])
    for export in (mixed.to_codex(), mixed.to_claude()):
        assert "e99" not in export.event_ids
        assert export.transformations[0].startswith("1 metadata record(s)")
        assert export.text == (history.to_codex() if export.format == "codex" else history.to_claude()).text
    text = mixed.to_markdown()
    assert '"e99"' not in text and "1 metadata record(s) without readable content omitted" in text
    nested = Session.from_events([event(0, text="```json\n{}\n```")])
    assert "````text" in nested.to_markdown()


def test_detached_search_scope_and_citations(history):
    result = history.search("syntaxerror", case_sensitive=False)[0]
    assert result.event.id == "e2" and result.trajectory.id == "e0"
    assert result.match_ranges == ((0, 11),)
    assert history.search("SyntaxError", mode="lexical").count() == 1
    assert history.split_into_trajectories()[1].search("SyntaxError").count() == 0
    assert history.search(status="failure", tool_name="shell").count() == 1
    assert "cached=0" in repr(history.search("Tests"))


@pytest.mark.parametrize("text", ["Check the constructor first", ""])
def test_foreign_thinking_is_portable_text_not_native_reasoning(text):
    history = Session.from_events([event(0, kind="thinking", text=text)])
    item = json.loads(history.to_codex().text)["payload"]
    assert item["type"] == "message" and item["role"] == "assistant"
    assert "[Historical thinking record]" in item["content"][0]["text"]
    assert (text or "No readable thinking text") in item["content"][0]["text"]
    with pytest.raises(UnsupportedConversion, match="signed content"):
        history.to_claude()


def test_store_pins_lazy_artifacts_and_result_membership(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    log = source / "session.jsonl"

    def write(text):
        log.write_text(json.dumps({"type": "user", "uuid": "u", "sessionId": "native",
                                   "message": {"role": "user", "content": text}}) + "\n")
    write("old SyntaxError")
    store = SessionStore(tmp_path / "index.sqlite")
    store.ingest(SourceConfig(id="fixture", harness="claude", root=source))
    sid = next(store.index.iter_sessions()).id
    old = store.get(sid)
    assert "events" not in old.__dict__
    pinned = store.search("SyntaxError")
    first = pinned[0]
    write("new SyntaxError")
    store.sync()
    new = store.get(sid)
    assert new.artifact_id != old.artifact_id
    assert store.search("SyntaxError")[0].event.text == "new SyntaxError"
    from click.testing import CliRunner

    from thearc.cli import main

    rendered = CliRunner().invoke(main, ["sessions", "--index", str(tmp_path / "index.sqlite"),
                                        "render", sid, "--trajectory", "0"])
    assert rendered.exit_code == 0, rendered.output
    assert "new SyntaxError" in rendered.output
    store.close()
    assert old.events[0].text == "old SyntaxError"
    assert new.events[0].text == "new SyntaxError"
    assert pinned.collect() == [first]
    assert old.search("old")[0].event.text == "old SyntaxError"


def test_input_mutations_do_not_change_history():
    supplied = event(0, role="user", arguments={"nested": [1]})
    session = Session.from_events([supplied])
    supplied.arguments["nested"].append(2)
    assert session.events[0].arguments["nested"] == (1,)


def test_markdown_prints_tool_arguments_once():
    call = event(0, kind="tool_call", tool_name="Bash", call_id="c", arguments={"command": "ls"},
                 text='{"command": "ls"}')
    text = Session.from_events([call]).to_markdown()
    assert text.count('"command"') == 1
