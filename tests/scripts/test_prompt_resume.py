"""Resume completed, failed, and externally interrupted batches without overwriting evidence."""

import fcntl
import json
from pathlib import Path

import pytest

from scripts import run_codex_prompts as collector
from tests.scripts.test_prompt_collection import setup as setup  # noqa: PLC0414


def interrupt_last_run(output):
    """Simulate an older collector killed before it finalized its last manifest."""
    summary = json.loads((output / "summary.json").read_text())
    last = summary["runs"].pop()
    for run in [*summary["runs"], last]:
        run.pop("prompt_index", None)
        if run is last:
            run["status"] = "running"
        (Path(run["run_dir"]) / "manifest.json").write_text(json.dumps(run))
    summary["status"] = "running"
    (output / "summary.json").write_text(json.dumps(summary))
    return last


def test_resume_keeps_completed_and_retries_orphan_at_original_commit(setup):
    repo, _, output, args = setup
    assert collector.main([*args, "First", "Second"]) == 0
    last = interrupt_last_run(output)
    before = json.loads((output / "summary.json").read_text())
    first_manifest = Path(before["runs"][0]["run_dir"]) / "manifest.json"
    first_bytes = first_manifest.read_bytes()
    last_events = Path(last["run_dir"]) / "events.jsonl"
    last_bytes = last_events.read_bytes()
    collector.git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "new HEAD after interruption",
    )

    assert collector.main([*args, "--resume", "Second"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["requested_prompts"] == ["First", "Second"]
    assert summary["status"] == "completed"
    assert summary["pending_prompts"] == []
    assert [r["status"] for r in summary["runs"]] == ["completed", "interrupted", "completed"]
    assert [r["prompt_index"] for r in summary["runs"]] == [0, 1, 1]
    assert summary["runs"][-1]["base_commit"] == before["base_commit"]
    assert summary["runs"][-1]["cwd"] != last["cwd"]
    assert first_manifest.read_bytes() == first_bytes
    assert last_events.read_bytes() == last_bytes
    assert Path(last["cwd"]).is_dir()
    assert json.loads((Path(last["run_dir"]) / "manifest.json").read_text())["status"] == "interrupted"
    # Repeating --resume never produces another session for an already completed prompt.
    assert collector.main([*args, "--resume", "First", "Second"]) == 0
    assert len(json.loads((output / "summary.json").read_text())["runs"]) == 3


def test_failed_attempt_and_duplicate_prompt_occurrences(setup, monkeypatch):
    _, _, output, args = setup
    monkeypatch.setenv("FAKE_MODE", "quota")
    assert collector.main([*args, "Same", "Same"]) == 1
    monkeypatch.delenv("FAKE_MODE")
    assert collector.main([*args, "--resume", "Same"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert [r["prompt_index"] for r in summary["runs"]] == [0, 0, 1]
    assert [r["status"] for r in summary["runs"]] == ["failed", "completed", "completed"]


def test_resume_recovers_completed_manifest_without_repeating_run(setup):
    _, _, output, args = setup
    assert collector.main([*args, "First", "Second"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    summary["runs"].pop()
    summary["status"] = "running"
    (output / "summary.json").write_text(json.dumps(summary))
    assert collector.main([*args, "--resume", "Second"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert len(summary["runs"]) == 2
    assert len(list((output / "runs").iterdir())) == 2


def test_resume_dry_run_does_not_rewrite_interrupted_artifacts(setup, capsys):
    _, _, output, args = setup
    assert collector.main([*args, "First", "Second"]) == 0
    last = interrupt_last_run(output)
    manifest = Path(last["run_dir"]) / "manifest.json"
    before = (output / "summary.json").read_bytes(), manifest.read_bytes()
    capsys.readouterr()
    assert collector.main([*args, "--resume", "--dry-run", "First", "Second"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["pending_prompts"] == ["Second"]
    assert plan["completed_prompts"] == 1
    assert before == ((output / "summary.json").read_bytes(), manifest.read_bytes())


def test_active_batch_cannot_be_resumed(setup, capsys):
    _, _, output, args = setup
    assert collector.main([*args, "First"]) == 0
    before = (output / "summary.json").read_bytes()
    with (output / ".collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            collector.main([*args, "--resume", "First"])
    assert "active collector or agent" in capsys.readouterr().err
    assert (output / "summary.json").read_bytes() == before


def test_resume_rejects_changed_prompt_and_different_harness(setup, capsys):
    repo, _, output, args = setup
    prompt = repo.parent / "prompt.md"
    prompt.write_text("Original")
    assert collector.main([*args, str(prompt)]) == 0
    before = (output / "summary.json").read_bytes()
    prompt.write_text("Changed")
    with pytest.raises(SystemExit):
        collector.main([*args, "--resume", str(prompt)])
    assert "Prompt changed" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        collector.main([*args, "--resume", "--harness", "claude", str(prompt)])
    assert "harness and baseline" in capsys.readouterr().err
    assert (output / "summary.json").read_bytes() == before
