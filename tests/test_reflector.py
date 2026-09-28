"""Shared reflection contracts for all input harnesses and execution backends."""

import builtins
import json

import pytest

from thearc import MetaAgent, Skill
from thearc.learning import (
    AgentReflector,
    Event,
    ReflectionBackend,
    ReflectionError,
    ReflectorConfig,
    Session,
    SessionBundle,
    SessionStore,
    SourceConfig,
    SourceReference,
    run_reflections,
)

AGENTS = ("codex", "claude", "pi", "antigravity")


def sample(harness="claude"):
    return SessionBundle(
        session=Session(id="session", native_id="native", source_id="source", harness=harness),
        events=[Event(id="event", session_id="session", run_id="run", source_id="source", harness=harness,
                      kind="message", role="assistant", text="Graphify guidance helped find the dependency.",
                      reference=SourceReference(path="unavailable", generation=0,
                                                byte_offset=0, byte_length=0, line=1))],
    )


def response(session_id="session", event_id="event"):
    return {"status": "completed", "final_response": json.dumps({
        "summary": "Test-only finding", "items": [{
            "target": {"kind": "skill", "name": "graphify", "section": "Query"},
            "rating": "helpful", "reason": "Guidance contributed to finding the dependency.",
            "evidence": [{"session_id": session_id, "event_id": event_id}], "limitations": [],
        }], "limitations": [],
    }), "thread_id": "test-thread", "usage": {"input_tokens": 10}}


def agent():
    return MetaAgent(name="agent", skills=[Skill(name="graphify", instructions="# Query\nInspect the graph.")])


def reflector(backend, runner=None, **kwargs):
    return AgentReflector(ReflectorConfig(model="test-model"),
                          backend=ReflectionBackend(agent=backend, name="fixture", version="1"),
                          runner=runner, **kwargs)


@pytest.mark.parametrize("backend", AGENTS)
@pytest.mark.parametrize("harness", AGENTS)
def test_every_backend_accepts_every_input_harness(backend, harness, monkeypatch):
    original_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("openai_codex"):
            pytest.fail("Shared reflection unexpectedly imported the Codex SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    calls = []

    def runner(prompt, schema, config):
        calls.append(prompt)
        assert schema["additionalProperties"] is False
        assert config.model == "test-model"
        assert "after" in prompt.lower()
        return response()

    result = reflector(backend, runner).reflect([sample(harness)], agent())
    assert len(calls) == 1
    assert result.items[0].target.section == "Query"
    assert result.raw["manifest"]["backend"]["agent"] == backend
    assert result.raw["execution"]["thread_id"] == "test-thread"


@pytest.mark.parametrize("backend", AGENTS)
def test_missing_backend_can_preview_but_cannot_execute(backend):
    r = reflector(backend)
    assert r.prepare([sample()], agent())["schema"]
    with pytest.raises(ReflectionError) as exc:
        r.reflect([sample()], agent())
    assert exc.value.code == "missing_runtime"


def test_cache_is_separated_by_backend_and_adapter_version(tmp_path):
    calls = []

    def runner(*args):
        calls.append(1)
        return response()

    ids = []
    for backend in AGENTS:
        r = reflector(backend, runner, artifact_dir=tmp_path, resume=True)
        result = r.reflect([sample()], agent())
        assert r.reflect([sample()], agent()).id == result.id
        ids.append(result.id)
    assert len(set(ids)) == len(calls) == 4
    r = AgentReflector(ReflectorConfig(model="test-model"), runner=runner,
                       backend=ReflectionBackend(agent="claude", name="fixture", version="2"),
                       artifact_dir=tmp_path, resume=True)
    assert r.reflect([sample()], agent()).id not in ids
    assert len(calls) == 5


@pytest.mark.parametrize("bad", [
    {"status": "failed", "final_response": "{}"},
    {"status": "completed", "final_response": "not json"},
    response(event_id="not-delivered"),
    None,
])
def test_shared_validation_rejects_invalid_provider_results(bad):
    with pytest.raises(ReflectionError):
        reflector("pi", lambda *args: bad).reflect([sample()], agent())


def test_generic_backend_works_with_snapshot_and_batch_flow(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "session.jsonl").write_text(json.dumps({
        "type": "assistant", "uuid": "a", "sessionId": "claude-native",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Graphify helped."}]},
    }) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="source", harness="claude", root=source))
        session = store.list_sessions()[0]
        evidence = store.snapshot(session_ids=[session.id])
        event = evidence.events[0]
        r = reflector("antigravity", lambda *args: response(session.id, event["id"]))
        assert r.prepare_snapshot(evidence, agent())["manifest"]["snapshot_sha256"] == evidence.sha256
        result = r.reflect_snapshot(evidence, agent())
        assert result.raw["manifest"]["snapshot_sha256"] == evidence.sha256
        result = run_reflections(store, agent(), r, tmp_path / "flow", execute=True)
        assert result["failed_batches"] == 0
