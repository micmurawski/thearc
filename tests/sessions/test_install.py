import json

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.sessions import Session, SessionAlreadyInstalled, Sessions

from .test_round_trip import SESSION, ev
from .test_sessions import claude_session, write_jsonl


def test_codex_install_is_a_copy_with_one_turn_per_trajectory(tmp_path):
    installed = SESSION.install("codex", home=tmp_path, strict=False)
    assert installed.path.parent == tmp_path / "sessions" / "2026" / "10" / "01"
    assert installed.path.name == f"rollout-2026-10-01T10-00-00-{installed.session_id}.jsonl"
    assert installed.command == f"codex resume {installed.session_id}" and installed.imported
    records = [json.loads(line) for line in installed.path.read_text().splitlines()]
    assert records[0]["payload"]["id"] == installed.session_id != "native-1"
    markers = [r["payload"]["type"] for r in records if r["type"] == "event_msg"]
    assert markers == ["task_started", "task_complete", "task_started", "task_complete"]
    assert records[-1]["payload"]["last_agent_message"] == "Fixed"


def test_each_trajectory_installs_as_its_own_session(tmp_path):
    first, second = (t.install("codex", home=tmp_path, strict=False) for t in SESSION.trajectories)
    assert first.session_id != second.session_id
    for installed in (first, second):
        markers = [json.loads(line)["payload"].get("type") for line in installed.path.read_text().splitlines()]
        assert markers.count("task_started") == 1


def test_installing_again_is_refused_unless_overwriting(tmp_path):
    first = SESSION.install("pi", home=tmp_path)
    with pytest.raises(SessionAlreadyInstalled, match="use overwrite"):
        SESSION.install("pi", home=tmp_path)
    again = SESSION.install("pi", home=tmp_path, overwrite=True)
    assert again.path == first.path and len(list((tmp_path / "sessions").rglob("*.jsonl"))) == 1


def test_claude_and_pi_locations_follow_the_working_directory(tmp_path):
    claude = SESSION.install("claude", home=tmp_path, strict=False)
    assert claude.path == tmp_path / "projects" / "-work" / f"{claude.session_id}.jsonl"
    assert claude.command == f"cd /work && claude --resume {claude.session_id}"
    pi = SESSION.install("pi", home=tmp_path)
    assert pi.path == tmp_path / "sessions" / "--work--" / f"2026-10-01T10-00-00-000Z_{pi.session_id}.jsonl"
    assert json.loads(pi.path.read_text().splitlines()[0])["id"] == pi.session_id


def test_opencode_is_written_for_import_and_antigravity_is_refused(tmp_path):
    opencode = SESSION.install("opencode", home=tmp_path)
    assert opencode.session_id.startswith("ses_") and not opencode.imported
    assert opencode.command.startswith("opencode import ") and opencode.path.suffix == ".json"
    with pytest.raises(ValueError, match="cannot install sessions into 'antigravity'"):
        SESSION.install("antigravity", home=tmp_path)


def test_keep_id_uses_the_source_identity(tmp_path):
    session = Session.from_events([ev(0, role="user", text="Hi", harness="claude")])
    assert session.install("pi", home=tmp_path, keep_id=True).session_id == "native-1"


def test_codex_sessions_keep_their_own_turns_but_take_the_copy_id(tmp_path):
    def codex(n, raw, **fields):
        return ev(n, harness="codex", raw=raw, **fields)

    header = {"type": "session_meta", "payload": {"id": "11111111-2222-3333-4444-555555555555", "cwd": "/w"}}
    started = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}}
    session = Session.from_events(
        [
            codex(0, header, kind="metadata", role=None),
            codex(1, started, kind="metadata", role=None),
            codex(2, {}, role="user", text="Go"),
        ]
    )
    installed = session.install("codex", home=tmp_path)
    records = [json.loads(line) for line in installed.path.read_text().splitlines()]
    assert records[0]["payload"]["id"] == installed.session_id
    assert [r["payload"].get("turn_id") for r in records if r["type"] == "event_msg"] == ["t1"]


@pytest.fixture
def index_with_claude(tmp_path):
    root = tmp_path / "claude"
    write_jsonl(root / "p" / "11111111-aaaa.jsonl", claude_session("11111111-aaaa"))
    write_jsonl(root / "p" / "22222222-bbbb.jsonl", claude_session("22222222-bbbb", "Second", "2026-10-03T10:00:00Z"))
    index = tmp_path / "index.sqlite"
    with Sessions(index) as sessions:
        sessions.add(root)
    return index


def test_cli_installs_one_trajectory_and_bulk(tmp_path, index_with_claude):
    runner = CliRunner()
    base = ["sessions", "--index", str(index_with_claude), "install", "--to", "codex", "--home", str(tmp_path / "c")]
    one = runner.invoke(main, [*base, "1111", "--trajectory", "0"])
    assert one.exit_code == 0, one.output
    assert "11111111-aaaa -> codex" in one.output and "codex resume" in one.output
    repeat = runner.invoke(main, [*base, "1111", "--trajectory", "0"])
    assert repeat.exit_code != 0 and "use overwrite" in repeat.output
    # The whole session holds the same events as its only trajectory: same selection, same copy, refused.
    clash = runner.invoke(main, [*base, "--all", "--harness", "claude"])
    assert clash.exit_code != 0 and "1 of 2 session(s) not installed" in clash.output
    bulk = runner.invoke(main, [*base[:-1], str(tmp_path / "bulk"), "--all", "--harness", "claude"])
    assert bulk.exit_code == 0, bulk.output
    assert len(list((tmp_path / "bulk" / "sessions").rglob("rollout-*.jsonl"))) == 2
    assert runner.invoke(main, [*base, "1111", "--all"]).exit_code != 0


def test_install_all_reports_each_session(tmp_path, index_with_claude):
    with Sessions(index_with_claude) as sessions:
        results = sessions.install_all("pi", home=tmp_path / "pi")
        assert all(not isinstance(outcome, Exception) for _, outcome in results) and len(results) == 2
        again = sessions.install_all("pi", home=tmp_path / "pi")
    assert all(isinstance(outcome, SessionAlreadyInstalled) for _, outcome in again)
