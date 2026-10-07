import json

import pytest
from click.testing import CliRunner

from thearc import MetaAgent
from thearc.cli import main
from thearc.resources import Sessions
from thearc.resources.sessions import detect_harness


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def claude_session(native, text="Run the tests", when="2026-10-01T10:00:00Z"):
    return [
        {"type": "user", "sessionId": native, "timestamp": when, "message": {"role": "user", "content": text}},
        {"type": "attachment", "sessionId": native, "attachment": {"type": "model", "text": "native state"}},
        {"type": "assistant", "sessionId": native, "message": {"role": "assistant", "content": "Done"}},
    ]


@pytest.fixture
def claude_dir(tmp_path):
    root = tmp_path / "claude"
    write_jsonl(root / "project" / "11111111-aaaa.jsonl", claude_session("11111111-aaaa"))
    write_jsonl(root / "project" / "22222222-bbbb.jsonl",
                claude_session("22222222-bbbb", "Second", "2026-10-03T10:00:00Z"))
    return root


@pytest.fixture
def codex_dir(tmp_path):
    root = tmp_path / "codex"
    write_jsonl(root / "2026" / "rollout-x.jsonl", [
        {"type": "session_meta", "timestamp": "2026-10-02T10:00:00Z", "payload": {"id": "codex-thread", "cwd": "/w"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "Fix it"}]}},
    ])
    return root


@pytest.fixture
def batch(tmp_path):
    root = tmp_path / "batch"
    write_jsonl(root / "sessions" / "run-1.jsonl", claude_session("33333333-cccc-dddd"))
    run = root / "runs" / "33333333ccccdddd"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"harness": "claude", "thread_id": "33333333-cccc-dddd",
                                                   "status": "completed"}))
    (run / "agent-before.json").write_text(MetaAgent(name="recorded").model_dump_json())
    return root


def test_detects_each_harness_and_refuses_ambiguity(tmp_path, claude_dir, codex_dir):
    assert detect_harness(claude_dir) == "claude"
    assert detect_harness(codex_dir) == "codex"
    write_jsonl(tmp_path / "opencode" / "s.jsonl", [{"info": {"id": "s"}, "messages": []}])
    assert detect_harness(tmp_path / "opencode") == "opencode"
    with pytest.raises(ValueError, match="claude, codex"):
        detect_harness(tmp_path)


def test_collects_multiple_harnesses_into_one_index(tmp_path, claude_dir, codex_dir, batch):
    with Sessions.open(tmp_path / "index.sqlite") as sessions:
        assert sessions.add(claude_dir).harness == "claude"
        assert sessions.add(codex_dir).harness == "codex"
        source = sessions.add(batch)
        assert source.root == (batch / "sessions").resolve()
        assert sessions.add(claude_dir) == sessions.sources()[1]  # re-adding reuses the registration
        assert len(sessions.sources()) == 3
        assert {s.harness for s in sessions.list()} == {"claude", "codex"}
        assert [s.native_id for s in sessions.list(harness="claude", since="2026-10-02")] == ["22222222-bbbb"]
        assert sessions.get().metadata.native_id == "22222222-bbbb"  # most recent

        by_native = sessions.get("codex-thread")
        assert sessions.get(by_native.id[:10]).id == by_native.id
        assert sessions.get("1111").metadata.native_id == "11111111-aaaa"
        recorded = sessions.get("33333333ccccdddd")  # collector run ID
        assert recorded.metadata.native_id == "33333333-cccc-dddd"
        assert sessions.agent(recorded).name == "recorded"
        assert sessions.agent(by_native) is None
        assert len(recorded.trajectories) == 1
        with pytest.raises(LookupError, match="matches no"):
            sessions.get("missing")


def test_index_must_stay_outside_source(claude_dir):
    with Sessions.open(claude_dir / "index.sqlite") as sessions, pytest.raises(ValueError, match="outside"):
        sessions.add(claude_dir)


def test_cli_add_list_and_render_by_any_reference(tmp_path, claude_dir, batch):
    index = str(tmp_path / "index.sqlite")
    runner = CliRunner()
    added = runner.invoke(main, ["sessions", "--index", index, "add", str(claude_dir), str(batch)])
    assert added.exit_code == 0, added.output
    assert [line.split()[0] for line in added.output.splitlines()] == ["claude", "claude"]
    assert "sessions" in runner.invoke(main, ["sessions", "--index", index, "sources"]).output
    listed = runner.invoke(main, ["sessions", "--index", index, "list", "--since", "2026-10-02"]).output
    assert len(listed.splitlines()) == 1 and "claude" in listed
    rendered = runner.invoke(main, ["sessions", "--index", index, "render", "1111", "--trajectory", "0"])
    assert rendered.exit_code == 0, rendered.output
    assert rendered.output.startswith("# Session 11111111-aaaa")
    assert "Run the tests" in rendered.output and "metadata record(s) without readable content" in rendered.output
    bare = runner.invoke(main, ["sessions", "--index", index, "render", "1111", "--no-header"]).output
    assert not bare.startswith("# Session")


def test_cli_handoff_previews_tools_with_recorded_agent(tmp_path, batch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Reflect")
    output = tmp_path / "out"
    result = CliRunner().invoke(main, [
        "reflection", "handoff", "--implementation", "codex", "--model", "m", "--prompt-file", str(prompt),
        "--output", str(output), "--index", str(tmp_path / "index.sqlite"), "--source", str(batch),
        "--session-id", "3333", "--trajectory", "0", "--tools", "mcp",
    ])
    assert result.exit_code == 0, result.output
    preview = json.loads((output / "preview.json").read_text())
    assert preview["manifest"]["tool_transport"] == "mcp"
    assert "submit_reflections" in preview["prompt"]


def test_cli_dumps_resource_catalog_for_recorded_agent(tmp_path, batch):
    index = str(tmp_path / "index.sqlite")
    runner = CliRunner()
    runner.invoke(main, ["sessions", "--index", index, "add", str(batch)])
    output = tmp_path / "resources.json"
    result = runner.invoke(main, ["reflection", "resources", "--index", index, "--session-id", "3333",
                                  "--format", "json", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == []  # the fixture agent has no resources
    agent = tmp_path / "agent.json"
    agent.write_text(MetaAgent(name="a", context={"AGENTS.md": {"content": "# Rules\nBe brief"}}).model_dump_json())
    listed = json.loads(runner.invoke(main, ["reflection", "resources", "--agent", str(agent),
                                             "--format", "json"]).output)
    assert {(e["target"]["kind"], e["target"]["section"]) for e in listed} == {("context", None), ("context", "Rules")}
    assert all(e["id"].startswith("res_") for e in listed)
    markdown = runner.invoke(main, ["reflection", "resources", "--agent", str(agent)]).output
    rules = next(e["id"] for e in listed if e["target"]["section"] == "Rules")
    assert "# context: AGENTS.md [context/AGENTS.md]" in markdown and "ResourceId" not in markdown
    assert f"# Rules [SectionId: {rules}]\n\nBe brief" in markdown
    ranked = runner.invoke(main, ["reflection", "resources", "--agent", str(agent), "--ranks"]).output
    assert f"# Rules [SectionId: {rules}, ranks: (harmful: 0, neutral: 0, helpful: 0)]" in ranked
    outline = runner.invoke(main, ["reflection", "resources", "--agent", str(agent), "--outline"]).output
    assert f"- Rules [SectionId: {rules}]" in outline and "Be brief" not in outline
    written = runner.invoke(main, ["reflection", "resources", "--agent", str(agent), "--format", "files",
                                   "--output", str(tmp_path / "files")])
    assert written.exit_code == 0, written.output
    assert (tmp_path / "files" / "context" / "AGENTS.md").read_text().startswith("# Rules")
