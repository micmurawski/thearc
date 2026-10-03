"""End-to-end collector checks with a fake CLI; no model calls or credentials."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import REPOSITORY_ROOT
from thearc.cli import main as cli

SCRIPT = REPOSITORY_ROOT / "scripts" / "run_codex_prompts.py"
spec = importlib.util.spec_from_file_location("collector", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    sessions = tmp_path / "codex-home" / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(sessions.parent))
    fake = tmp_path / "fake-codex"
    fake.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys, time, uuid
from pathlib import Path
mode = os.environ.get("FAKE_MODE", "success")
prompt = sys.stdin.read()
if os.environ.get("FAKE_EDIT"):
    assert Path("tracked.txt").read_text() == "baseline"
    assert not Path("new-output.txt").exists()
    Path("tracked.txt").write_text(prompt)
    Path("new-output.txt").write_text(prompt)
thread = str(uuid.uuid4())
def emit(data):
    print(json.dumps(data), flush=True)
emit({"type": "thread.started", "thread_id": thread})
if mode == "timeout":
    time.sleep(30)
records = [{"type": "session_meta", "payload": {"id": thread, "cwd": str(Path.cwd())}},
           {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": prompt}]}}]
if mode in {"success", "missing", "wrong-cwd", "empty-native"}:
    emit({"type": "item.completed", "item": {"id": "tool", "type": "command_execution"}})
    if mode != "empty-native":
        records.append({"type": "response_item", "payload": {"type": "function_call", "call_id": "tool",
                        "name": "shell", "arguments": "ls"}})
if mode not in {"quota", "empty"}:
    emit({"type": "item.completed", "item": {"id": "reply", "type": "agent_message", "text": "Done"}})
    if mode != "empty-native":
        records.append({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}]}})
if mode == "wrong-cwd":
    records[0]["payload"]["cwd"] = "/some/other/project"
if mode != "missing":
    path = Path(os.environ["CODEX_HOME"]) / "sessions" / f"rollout-test-{thread}.jsonl"
    path.write_text("\\n".join(map(json.dumps, records)) + "\\n")
if mode == "quota":
    emit({"type": "turn.failed", "error": {"message": "usage_limit_exceeded"}})
    sys.exit(1)
emit({"type": "turn.completed"})
''')
    fake.chmod(0o755)
    output = tmp_path / "output"
    return repo, sessions, output, ["--cwd", str(repo), "--codex-bin", str(fake), "--output", str(output)]


def test_success_collects_exact_sessions_and_indexes_them(setup):
    repo, sessions, output, args = setup
    # This unrelated session must never be selected, regardless of modification time.
    (sessions / "rollout-other.jsonl").write_text('{"type":"session_meta","payload":{"id":"other"}}\n')
    before = collector.git(repo, "rev-parse", "HEAD")
    assert collector.main([*args, "Inspect files", "Write tests"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["not_started"] == []
    assert len(summary["runs"]) == 2
    assert len(list((output / "sessions").glob("*.jsonl"))) == 2
    assert (output / "index.sqlite").exists()
    for run in summary["runs"]:
        assert run["indexed_tool_events"] == 1
        assert run["indexed_activity_events"] == 2
        assert run["thread_id"] in run["transcript"]
        assert (Path(run["run_dir"]) / "agent-before.json").exists()
    assert collector.git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("mode,reason", [
    ("quota", "agent_execution_failed"), ("empty", "no_assistant_or_tool_activity"),
    ("missing", "matching_transcript_missing"), ("wrong-cwd", "matching_transcript_missing"),
    ("empty-native", "no_indexed_activity"), ("response-only", "no_tool_activity"),
    ("timeout", "timeout"),
])
def test_failure_stops_batch_and_keeps_diagnostics(setup, monkeypatch, mode, reason):
    _, _, output, args = setup
    monkeypatch.setenv("FAKE_MODE", mode)
    assert collector.main([*args, "--timeout", "0.1" if mode == "timeout" else "10", "First", "Second"]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["not_started"] == ["Second"]
    assert len(summary["runs"]) == 1
    assert summary["runs"][0]["failure_reason"] == reason
    assert (Path(summary["runs"][0]["run_dir"]) / "stderr.log").exists()


def test_intentional_response_only(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "response-only")
    assert collector.main([*setup[3], "--allow-response-only", "Say hello"]) == 0


def test_dry_run_and_missing_prompt_file(setup):
    _, _, output, args = setup
    assert collector.main([*args, "--dry-run", "Inspect files"]) == 0
    assert not output.exists()
    with pytest.raises(SystemExit):
        collector.main([*args, "does-not-exist.md"])
    assert not output.exists()
    assert collector.extract_prompt("long prompt " * 500) == ("long prompt " * 500).strip()


def test_malformed_stream_and_multiple_threads(tmp_path):
    stream = tmp_path / "events.jsonl"
    stream.write_text('not json\n[]\n{"type":"thread.started","thread_id":"a"}\n'
                      '{"type":"thread.started","thread_id":"b"}\n')
    result = collector.inspect_stream(stream)
    assert result["malformed_stream"] is True
    assert result["thread_id"] is None


def test_new_and_legacy_cli_names():
    from click.testing import CliRunner

    for command in ("sessions", "history"):
        result = CliRunner().invoke(cli, [command, "index", "--help"])
        assert result.exit_code == 0
        assert "antigravity" in result.output


def test_wrapper_from_another_directory(setup, tmp_path):
    wrapper = SCRIPT.with_suffix(".sh")
    result = subprocess.run([str(wrapper), *setup[3], "--dry-run", "prompts/01_add_request_id_header.md"],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_uninitialized_nested_checkout_is_rejected(setup):
    repo, _, output, args = setup
    nested = repo / "uninitialized-submodule"
    nested.mkdir()
    with pytest.raises(SystemExit):
        collector.main([*args, "--cwd", str(nested), "--dry-run", "Inspect files"])
    assert not output.exists()


def test_worktrees_isolate_prompts_and_preserve_dirty_source(setup, monkeypatch):
    repo, _, output, args = setup
    (repo / "tracked.txt").write_text("baseline")
    collector.git(repo, "add", "tracked.txt")
    collector.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture")
    before_head = collector.git(repo, "rev-parse", "HEAD")
    before_branch = collector.git(repo, "symbolic-ref", "HEAD")
    (repo / "tracked.txt").write_text("staged user edit")
    collector.git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("unstaged user edit")
    (repo / "untracked.txt").write_text("user data")
    before_status = collector.git(repo, "status", "--porcelain")
    before_index = collector.git(repo, "diff", "--cached")
    monkeypatch.setenv("FAKE_EDIT", "1")
    assert collector.main([*args, "First", "Second"]) == 0
    runs = json.loads((output / "summary.json").read_text())["runs"]
    assert runs[0]["cwd"] != runs[1]["cwd"]
    for run, prompt in zip(runs, ["First", "Second"]):
        worktree = Path(run["cwd"])
        assert worktree != repo
        assert run["base_commit"] == before_head
        assert run["git_status_before"] == ""
        assert collector.git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
        assert (worktree / "tracked.txt").read_text() == prompt
        assert (worktree / "new-output.txt").read_text() == prompt
        assert not (worktree / "untracked.txt").exists()
        assert prompt in (Path(run["run_dir"]) / "changes.patch").read_text()
    assert (repo / "tracked.txt").read_text() == "unstaged user edit"
    assert (repo / "untracked.txt").read_text() == "user data"
    assert collector.git(repo, "status", "--porcelain") == before_status
    assert collector.git(repo, "diff", "--cached") == before_index
    assert collector.git(repo, "rev-parse", "HEAD") == before_head
    assert collector.git(repo, "symbolic-ref", "HEAD") == before_branch
