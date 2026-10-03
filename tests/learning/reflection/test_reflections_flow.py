"""Standalone corpus flow contracts, with real indexing and offline SDK responses."""

import json
from pathlib import Path

import pytest

from thearc import MetaAgent, Skill
from thearc.learning import (
    CodexReflector,
    CodexReflectorConfig,
    ReflectionError,
    SessionStore,
    SourceConfig,
    run_reflections,
)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    for number in range(3):
        records = [
            {"type": "session_meta", "payload": {"id": f"session-{number}"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Inspect the graph"}]}},
        ]
        if number < 2:
            records.append({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "I inspected the graph."}]}})
        else:
            records.append({"type": "event_msg", "payload": {"type": "task_complete",
                            "error": {"codex_error_info": "usage_limit_exceeded"}}})
        (root / f"rollout-{number}.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    with SessionStore(tmp_path / "history.sqlite") as history:
        history.register_source(SourceConfig(id="experiment", harness="codex", root=root))
        history.sync()
        yield history


def setup_reflector(runner):
    return CodexReflector(CodexReflectorConfig(model="test-model"), sdk_runner=runner)


def response(*args):
    return {"status": "completed", "final_response": json.dumps({
        "summary": "Inspected sessions", "items": [], "limitations": ["No observed skill use"],
    })}


def agent():
    return MetaAgent(name="agent", skills=[Skill(name="graphify", instructions="Inspect the graph.")])


def test_preview_covers_entire_corpus_without_inference(corpus, tmp_path):
    def forbidden(*args):
        pytest.fail("Dry-run invoked SDK")

    result = run_reflections(corpus, agent(), setup_reflector(forbidden), tmp_path / "output")
    assert result["status"] == "prepared"
    assert (result["selected_sessions"], result["eligible_sessions"], result["skipped_sessions"]) == (3, 2, 1)
    attempt = Path(result["attempt_dir"])
    skipped = [s for s in json.loads((attempt / "selection.json").read_text()) if s["status"] == "skipped"]
    assert skipped[0]["source_errors"] == ["usage_limit_exceeded"]
    assert len(list(attempt.glob("batch-*.json"))) == 1
    assert not list((tmp_path / "output").glob("reflection-*"))


def test_execution_resume_and_no_mutation(corpus, tmp_path):
    calls = []

    def sdk(*args):
        calls.append(args)
        return response()

    config = agent()
    before = config.model_dump_json()
    reflector = setup_reflector(sdk)
    output = tmp_path / "output"
    first = run_reflections(corpus, config, reflector, output, batch_size=1, execute=True)
    assert first["status"] == "completed"
    assert len(calls) == 2
    assert config.model_dump_json() == before
    for batch in first["batches"]:
        assert (output / batch["artifact"]).is_file()
    second = run_reflections(corpus, config, reflector, output, batch_size=1, execute=True, resume=True)
    assert second["batches"] == first["batches"]
    assert len(calls) == 2
    assert second["attempt_dir"] != first["attempt_dir"]


def test_failed_batch_does_not_stop_other_batches(corpus, tmp_path, monkeypatch):
    reflector = setup_reflector(response)
    reflect = reflector.reflect
    calls = []

    def fail_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise ReflectionError("sdk_failed", "Test failure")
        return reflect(*args)

    monkeypatch.setattr(reflector, "reflect", fail_once)
    result = run_reflections(corpus, agent(), reflector, tmp_path / "output", execute=True, batch_size=1)
    assert result["status"] == "partial_failure"
    assert result["failed_batches"] == 1
    assert [b["status"] for b in result["batches"]] == ["failed", "completed"]


def test_input_budget_splits_batches(corpus, tmp_path, monkeypatch):
    reflector = setup_reflector(response)
    prepare = reflector.prepare

    def limited(batch, config):
        if len(batch) > 1:
            raise ReflectionError("input_too_large", "Test limit")
        return prepare(batch, config)

    monkeypatch.setattr(reflector, "prepare", limited)
    result = run_reflections(corpus, agent(), reflector, tmp_path / "output")
    assert len(result["batches"]) == 2


@pytest.mark.parametrize("options", [{"batch_size": 0}, {"source_ids": ["unknown"]}])
def test_invalid_selection_rejected(corpus, tmp_path, options):
    with pytest.raises(ValueError):
        run_reflections(corpus, agent(), setup_reflector(response), tmp_path / "output", **options)


def test_output_cannot_contaminate_evidence(corpus, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        run_reflections(corpus, agent(), setup_reflector(response), tmp_path / "sessions" / "output")


def test_empty_evidence_is_not_reported_as_success(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(corpus, "iter_events", lambda *args: iter([]))

    def forbidden(*args):
        pytest.fail("No-evidence run invoked SDK")

    result = run_reflections(corpus, agent(), setup_reflector(forbidden), tmp_path / "output", execute=True)
    assert result["status"] == "no_evidence"
    assert result["skipped_sessions"] == 3
    assert result["batches"] == []
