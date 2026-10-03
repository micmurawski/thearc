"""Local-only ACE graph tests, with no dependency on the captured corpus or SDK."""

import json

import pytest

from cookbook import graphify_ace
from thearc import MetaAgent, Skill
from thearc.learning.curation import load_epoch


@pytest.fixture
def corpus(tmp_path):
    corpus = tmp_path / "corpus"
    sessions = corpus / "sessions"
    sessions.mkdir(parents=True)
    agent = MetaAgent(name="graphify", skills=[Skill(
        name="graphify", instructions="# Graphify\n\nQuery the graph.\n",
        files={"references/query.md": "# Query\n\nUse graphify query.\n", ".graphify_version": "1.0"},
    )])
    runs = []
    for number in range(5):
        identity = f"thread-{number}"
        run = corpus / "runs" / identity
        run.mkdir(parents=True)
        (run / "agent-before.json").write_text(agent.model_dump_json())
        runs.append({"thread_id": identity, "status": "completed", "has_activity": True, "run_dir": str(run)})
        records = [
            {"type": "session_meta", "payload": {"id": identity}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Inspect graphify query results"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Inspected graphify"}]}},
        ]
        (sessions / f"rollout-{identity}.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    (corpus / "summary.json").write_text(json.dumps({"runs": runs}))
    return corpus


def run_recipe(corpus, output, mode):
    graphify_ace.main(["--corpus", str(corpus), "--output", str(output), "--mode", mode,
                       "--result-version", "1.0-ace.1", "--batch-size", "2"])


@pytest.mark.parametrize("mode", ["preview", "offline"])
def test_recipe_flow_batches_and_artifacts(corpus, tmp_path, monkeypatch, mode):
    def forbidden(*args, **kwargs):
        pytest.fail("Recipe attempted live inference")

    monkeypatch.setattr(graphify_ace.CodexCurator, "edit", forbidden)
    if mode == "preview":
        monkeypatch.setattr(graphify_ace.CodexReflector, "reflect", forbidden)
    original = {str(path): path.read_bytes() for path in corpus.rglob("*") if path.is_file()}
    output = tmp_path / mode
    run_recipe(corpus, output, mode)
    manifest = json.loads((output / "batches.json").read_text())
    assert [len(batch["session_ids"]) for batch in manifest["batches"]] == [2, 2, 1]
    assert len({sid for batch in manifest["batches"] for sid in batch["session_ids"]}) == 5
    html = (output / "flow.html").read_text()
    assert "load_session" in html and "inspect_batch" in html
    assert "5/5" in html and "3/3" in html
    assert original == {str(path): path.read_bytes() for path in corpus.rglob("*") if path.is_file()}
    if mode == "preview":
        assert "finish_preview" in html and "curate_epoch" not in html
        assert len(list(output.glob("batch-*.json"))) == 3
        assert not (output / "curation").exists()
        assert not (output / "result.json").exists()
    else:
        assert "curate_epoch" in html
        result = json.loads((output / "result.json").read_text())
        assert len(result["sessions"]) == 5 and len(result["reflections"]) == 3
        assert len(result["curations"]) == 1
        epoch = load_epoch(output / "curation")
        assert epoch["agent"]["version"] == "1.0-ace.1"
        assert len(epoch["reflections"]) == 3
    with pytest.raises(FileExistsError):
        run_recipe(corpus, output, mode)


def test_failed_batch_keeps_artifacts_without_commit(corpus, tmp_path, monkeypatch):
    original = graphify_ace.offline_reflection
    calls = []

    def fail_second(*args):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("Synthetic batch failure")
        return original(*args)

    monkeypatch.setattr(graphify_ace, "offline_reflection", fail_second)
    output = tmp_path / "failed"
    with pytest.raises(Exception, match="Synthetic batch failure"):
        run_recipe(corpus, output, "offline")
    assert len(calls) == 2
    assert len(list((output / "reflections").rglob("reflection.json"))) == 1
    assert not (output / "curation/HEAD.json").exists()
    assert not (output / "result.json").exists()
    html = (output / "flow.html").read_text()
    assert "Synthetic batch failure" in html
    assert "1/3" in html
