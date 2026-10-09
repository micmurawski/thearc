import json

import pytest
from click.testing import CliRunner

from thearc.cli import main

from .test_sessions import claude_session, write_jsonl


@pytest.fixture
def claude_dir(tmp_path):
    root = tmp_path / "claude"
    write_jsonl(root / "project" / "11111111-aaaa.jsonl", claude_session("11111111-aaaa"))
    write_jsonl(
        root / "project" / "22222222-bbbb.jsonl", claude_session("22222222-bbbb", "Second", "2026-10-03T10:00:00Z")
    )
    return root


@pytest.fixture
def cli(tmp_path):
    runner = CliRunner()
    index = str(tmp_path / "index.sqlite")

    def run(*args, ok=True):
        result = runner.invoke(main, ["sessions", "--index", index, *args])
        if ok:
            assert result.exit_code == 0, result.output
        return result

    return run


def test_add_list_and_render_by_any_reference(cli, claude_dir, tmp_path):
    batch = tmp_path / "batch"
    write_jsonl(batch / "sessions" / "run-1.jsonl", claude_session("33333333-cccc-dddd"))
    (batch / "runs" / "33333333ccccdddd").mkdir(parents=True)
    (batch / "runs" / "33333333ccccdddd" / "manifest.json").write_text(
        json.dumps({"harness": "claude", "thread_id": "33333333-cccc-dddd"})
    )

    added = cli("add", str(claude_dir), str(batch))
    assert [line.split()[0] for line in added.output.splitlines()] == ["claude", "claude"]
    assert "sessions" in cli("sources").output
    listed = cli("list", "--since", "2026-10-02").output
    assert len(listed.splitlines()) == 1 and "claude" in listed
    rendered = cli("render", "1111", "--trajectory", "0").output
    assert rendered.startswith("# Session 11111111-aaaa")
    assert "Run the tests" in rendered and "metadata record(s) without readable content" in rendered
    assert not cli("render", "1111", "--no-header").output.startswith("# Session")
    assert "Run the tests" in cli("render", "33333333ccccdddd").output  # collector run ID


def test_trajectories_and_render_formats(cli, claude_dir):
    cli("add", str(claude_dir))
    listed = cli("trajectories", "1111").output.splitlines()
    assert listed[0] == "11111111-aaaa: 1 trajectories, 0 unassigned events"
    assert listed[1].split()[:3] == ["0", "end_of_stream", "2"] and "Run the tests" in listed[1]
    codex = cli("render", "1111", "--format", "codex").output.splitlines()
    assert all(json.loads(line) for line in codex)
    assert cli("render", "1111", "--trajectory", "0", "--through").output.startswith("# Session")


def test_search_by_text_and_filters(cli, claude_dir):
    cli("add", str(claude_dir))
    hits = cli("search", "Second").output.splitlines()
    assert len(hits) == 1 and "Second" in hits[0]
    assert cli("search", "second").output == ""
    assert len(cli("search", "second", "--ignore-case").output.splitlines()) == 1
    assert len(cli("search", "second", "--mode", "lexical").output.splitlines()) == 1
    users = cli("search", "--role", "user", "--json").output.splitlines()
    assert {json.loads(line)["native_session_id"] for line in users} == {"11111111-aaaa", "22222222-bbbb"}
    first = json.loads(users[0])
    assert first["trajectory_id"] == first["event"]["id"]
    assert len(cli("search", "--role", "user", "--limit", "1").output.splitlines()) == 1

    shown = cli("show", first["event"]["id"], "--context", "1").output.splitlines()
    assert first["event"]["id"] in {json.loads(line)["id"] for line in shown}
    trace = json.loads(cli("trace", first["event"]["run_id"]).output)
    assert trace["run_id"] == first["event"]["run_id"]


def test_sync_picks_up_new_transcripts(cli, claude_dir):
    cli("add", str(claude_dir))
    write_jsonl(claude_dir / "project" / "44444444-eeee.jsonl", claude_session("44444444-eeee", "Third"))
    assert json.loads(cli("sync").output)["events_added"] > 0
    assert "Third" in cli("render", "4444").output


def test_errors_are_reported_without_tracebacks(cli, claude_dir):
    assert "No sessions are indexed" in cli("render", ok=False).output
    cli("add", str(claude_dir))
    for args, message in [
        (("render", "missing"), "matches no indexed session"),
        (("render", "1111", "--trajectory", "5"), "Session has 1 trajectories; use 0..0"),
        (("render", "1111", "--through"), "--through requires --trajectory"),
        (("search",), "give TEXT or at least one filter"),
        (("show", "nope"), "Unknown event: nope"),
        (("trace", "nope"), "Unknown run: nope"),
        (("add", str(claude_dir), str(claude_dir), "--source-id", "x"), "--source-id applies to a single path"),
    ]:
        result = cli(*args, ok=False)
        assert result.exit_code != 0 and message in result.output, (args, result.output)
        assert "Traceback" not in result.output
