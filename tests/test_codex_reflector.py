"""Offline contracts: model inspection is required; no credentials or inference."""

import inspect
import json
import os
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest

from thearc import ContextDocument, Hook, MetaAgent, Skill
from thearc.learning import (
    AceConfig,
    AcePipeline,
    CodexReflector,
    CodexReflectorConfig,
    Event,
    HistorySessionMaterializer,
    ReflectionError,
    ReflectionRating,
    Session,
    SessionBundle,
    SessionStore,
    SourceConfig,
    SourceReference,
)
from thearc.learning.reflection.providers.codex import (
    _DISABLED_FEATURES,
    SDK_VERSION,
    ReflectionOutput,
    _run_sdk,
    redact,
    reflection_schema,
)


@pytest.fixture
def agent():
    return MetaAgent(
        name="agent",
        skills=[Skill(name="graphify", instructions="# Query\nInspect the graph before searching.",
                      files={"references/query.md": "# Query\nRun a targeted query."})],
        hooks=[Hook(name="before-bash", command="graphify hook-check")],
        context=[ContextDocument(filename="AGENTS.md", content="# Graphify\nUse graphify.")],
    )


def bundle(harness="codex", count=4):
    session = Session(id=f"{harness}:session", source_id=harness, harness=harness, native_id="same-native-id")
    events = [Event(
        id=f"{harness}:e{i}", source_id=harness, harness=harness, session_id=session.id,
        run_id=f"{harness}:run", kind="message", role="user" if i == 0 else "assistant",
        text="Inspect graphify" if i == 0 else f"Outcome {i}",
        reference=SourceReference(path="/private/source.jsonl", generation=0, byte_offset=i,
                                  byte_length=1, line=i + 1),
    ) for i in range(count)]
    if count >= 4:
        events[1] = events[1].model_copy(update={
            "kind": "tool_call", "call_id": "call", "tool_name": "shell", "arguments": {"command": "graphify query"},
        })
        events[2] = events[2].model_copy(update={
            "kind": "tool_result", "call_id": "call", "role": "tool", "text": "query completed", "status": "success",
        })
    return SessionBundle(session=session, events=events)


def response(finding=True):
    items = [{
        "rating": "helpful", "reason": "The query instructions enabled a targeted graph lookup.",
        "target": {"kind": "skill_file", "name": "graphify/references/query.md", "section": "Query"},
        "evidence": [{"session_id": "codex:session", "event_id": "codex:e2"}],
        "limitations": ["This does not establish task success."],
    }] if finding else []
    return {"status": "completed", "thread_id": "reflection-thread", "turn_id": "reflection-turn",
            "final_response": json.dumps({"summary": "Inspected supplied sessions.", "items": items,
                                          "limitations": ["Historical configuration is unknown."]})}


def reflector(**kwargs):
    return CodexReflector(CodexReflectorConfig(model="test-model"), **kwargs)


def test_sdk_inspection_precedes_validated_reflection(agent, tmp_path):
    calls = []
    before = agent.model_dump_json()

    def sdk(prompt, schema, config):
        calls.append("prompted")
        assert "After receiving this prompt, inspect" in prompt
        assert "codex:e2" in prompt and "graphify/references/query.md" in prompt
        assert schema["additionalProperties"] is False
        assert config.model == "test-model"
        return response()

    result = reflector(sdk_runner=sdk, artifact_dir=tmp_path).reflect([bundle()], agent)
    assert calls == ["prompted"]
    assert result.items[0].target.section == "Query"
    assert result.evidence_event_ids == ["codex:e2"]
    assert result.rank_proposals == []
    assert agent.model_dump_json() == before
    folder = tmp_path / result.id
    assert (folder / "input.json").exists()
    assert json.loads((folder / "reflection.json").read_text())["id"] == result.id
    records = [json.loads(line) for line in (folder / "run.jsonl").read_text().splitlines()]
    assert [r["event"] for r in records] == ["reflection_started", "sdk_attempt_started", "reflection_completed"]


@pytest.mark.parametrize("harness", ["codex", "claude", "pi", "antigravity"])
def test_each_normalized_harness_and_mixed_sources(harness, agent):
    r = reflector(sdk_runner=lambda *args: response(False))
    result = r.reflect([bundle(harness)], agent)
    assert result.session_ids == [f"{harness}:session"]
    prepared = r.prepare([bundle(h) for h in ["codex", "claude", "pi", "antigravity"]], agent)
    assert len(set(prepared["manifest"]["session_ids"])) == 4


def test_schema_is_closed_and_all_properties_required():
    schema = reflection_schema()

    def check(node):
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(schema)
    assert schema["$defs"]["ReflectionRating"]["enum"] == ["helpful", "neutral", "harmful"]
    assert schema["$defs"]["ReflectionItem"]["properties"]["target"] == {"$ref": "#/$defs/ResourceTarget"}
    ReflectionOutput.model_validate_json(response()["final_response"])


@pytest.mark.parametrize("rating", list(ReflectionRating))
@pytest.mark.parametrize("target", [
    {"kind": "context", "name": "AGENTS.md", "section": "Graphify"},
    {"kind": "hook", "name": "before-bash", "section": None},
])
def test_ratings_address_specific_configuration_parts_without_updating_ranks(rating, target, agent):
    before = agent.model_dump_json()
    envelope = response()
    output = json.loads(envelope["final_response"])
    output["items"][0].update(target=target, rating=rating.value,
                              reason="This guidance affected the observed graph lookup as described by the evidence.")
    envelope["final_response"] = json.dumps(output)
    result = reflector(sdk_runner=lambda *a: envelope).reflect([bundle()], agent)
    assert result.items[0].rating == rating
    assert result.items[0].target.model_dump() == target
    assert result.model_dump(mode="json")["items"][0]["rating"] == rating.value
    assert agent.model_dump_json() == before


def test_neutral_is_not_a_substitute_for_missing_evidence(agent):
    envelope = response()
    output = json.loads(envelope["final_response"])
    output["items"][0].update(rating="neutral", evidence=[])
    envelope["final_response"] = json.dumps(output)
    with pytest.raises(ReflectionError, match="requires session evidence"):
        reflector(sdk_runner=lambda *a: envelope).reflect([bundle()], agent)


@pytest.mark.parametrize("change,code", [
    ({"evidence": [{"session_id": "claude:session", "event_id": "codex:e2"}]}, "invalid_evidence"),
    ({"evidence": []}, "invalid_evidence"),
    ({"reason": " "}, "invalid_evidence"),
    ({"target": None}, "invalid_output"),
    ({"rating": "success"}, "invalid_output"),
    ({"target": {"kind": "skill", "name": "missing", "section": None}}, "invalid_target"),
    ({"target": {"kind": "skill", "name": "graphify", "section": "missing"}}, "invalid_target"),
])
def test_invalid_findings_fail_without_success_artifact(change, code, agent, tmp_path):
    envelope = response()
    output = json.loads(envelope["final_response"])
    output["items"][0].update(change)
    envelope["final_response"] = json.dumps(output)
    with pytest.raises(ReflectionError) as exc:
        reflector(sdk_runner=lambda *a: envelope, artifact_dir=tmp_path).reflect([bundle()], agent)
    assert exc.value.code == code
    assert not list(tmp_path.glob("*/reflection.json"))
    assert "reflection_failed" in next(tmp_path.glob("*/run.jsonl")).read_text()


def test_ambiguous_section_and_omitted_event_are_rejected(agent):
    agent.skills["graphify"].files["references/query.md"] += "\n# Query\nRepeated heading."
    with pytest.raises(ReflectionError, match="uniquely"):
        reflector(sdk_runner=lambda *a: response()).reflect([bundle()], agent)
    agent.skills["graphify"].files["references/query.md"] = "# Query\nUnique"
    r = CodexReflector(CodexReflectorConfig(model="test", max_events_per_session=2),
                      sdk_runner=lambda *a: response())
    with pytest.raises(ReflectionError, match="supplied session"):
        r.reflect([bundle()], agent)


def test_preparation_keeps_task_outcome_and_whole_pairs(agent):
    r = CodexReflector(CodexReflectorConfig(model="test", max_events_per_session=3))
    prepared = r.prepare([bundle(count=8)], agent)
    session = prepared["evidence"]["sessions"][0]
    ids = [e["id"] for e in session["events"]]
    assert "codex:e0" in ids and "codex:e7" in ids
    assert "codex:e1" not in ids and "codex:e2" not in ids
    assert "codex:e2" in session["omitted_event_ids"]


def test_materializer_does_not_discard_final_outcome():
    original = bundle(count=8)
    history = SimpleNamespace(iter_events=lambda *a: iter(original.events))
    result = HistorySessionMaterializer(history).materialize(
        original.session, AceConfig(max_events_per_session=3, max_text_chars_per_event=5),
    )
    assert result.events[-1].id == "codex:e7"
    assert result.omitted_event_ids and result.truncated_event_ids
    assert original.events[-1].text == "Outcome 7"


def test_redaction_private_fields_and_input_limits(agent):
    b = bundle()
    b.events[0].text = "api_key=secretvalue Bearer abcdefg /Users/alice/private"
    b.events[0].raw = {"secret": "raw-payload-never-sent"}
    b.events[1].arguments = {"api_key": "argument-secret"}
    b.events[3].kind = "thinking"
    b.events[3].text = "hidden-reasoning-never-sent"
    prepared = reflector().prepare([b], agent)
    rendered = json.dumps(prepared)
    for secret in ["secretvalue", "abcdefg", "alice", "raw-payload-never-sent", "argument-secret",
                   "hidden-reasoning-never-sent", "/private/source.jsonl"]:
        assert secret not in rendered
    assert "[REDACTED]" in rendered
    assert redact({"Authorization": "Bearer abc"}) == {"Authorization": "[REDACTED]"}
    with pytest.raises(ReflectionError, match="exceeds limit"):
        CodexReflector(CodexReflectorConfig(model="test", max_input_chars=1000)).prepare([b], agent)


def test_deterministic_input_hash_and_nested_resources(agent):
    r = reflector()
    a = r.prepare([bundle()], agent)
    b = r.prepare([bundle()], agent)
    assert a == b
    kinds = {item["target"]["kind"] for item in a["evidence"]["resources"]}
    assert kinds == {"skill", "skill_file", "context", "hook"}
    agent.skills["graphify"].files["references/query.md"] += " Changed"
    assert r.prepare([bundle()], agent)["manifest"]["config_sha256"] != a["manifest"]["config_sha256"]


@pytest.mark.parametrize("envelope,code", [
    ({"status": "interrupted"}, "incomplete"),
    ({"status": "completed", "final_response": "not JSON"}, "invalid_output"),
    ({"status": "completed", "final_response": None}, "invalid_output"),
    ({"status": "completed", "final_response": '{}'}, "invalid_output"),
])
def test_incomplete_and_invalid_sdk_output(envelope, code, agent):
    with pytest.raises(ReflectionError) as exc:
        reflector(sdk_runner=lambda *args: envelope).reflect([bundle()], agent)
    assert exc.value.code == code


def test_retry_only_explicit_transient_failures(agent):
    attempts = []

    def sdk(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise ReflectionError("transient", "busy")
        return response(False)

    r = CodexReflector(CodexReflectorConfig(model="test", max_retries=1), sdk_runner=sdk)
    assert r.reflect([bundle()], agent).items == []
    assert len(attempts) == 2


def test_reflection_only_never_calls_curator_or_mutates(agent):
    before = agent.model_dump_json()
    b = bundle()

    def forbidden(*args):
        raise AssertionError("No curation, installation, or change callback permitted")

    result = AcePipeline(
        SimpleNamespace(select=lambda q: [b.session]),
        SimpleNamespace(materialize=lambda s, c: b),
        reflector(sdk_runner=lambda *args: response()),
        SimpleNamespace(curate=forbidden), on_change=forbidden,
    ).run(agent, reflection_only=True)
    assert result.curations == [] and result.update.applied_curation_ids == []
    assert result.agent.model_dump_json() == before == agent.model_dump_json()


def test_pinned_sdk_contract_without_running_a_model():
    sdk = pytest.importorskip("openai_codex")
    assert sdk.__version__ == SDK_VERSION
    assert "output_schema" in inspect.signature(sdk.Thread.run).parameters
    assert "config_overrides" in inspect.signature(sdk.CodexConfig).parameters


@pytest.mark.parametrize("mode", ["success", "disabled_mcp", "active_mcp", "features", "tool", "timeout"])
def test_real_sdk_boundary_uses_prompt_schema_and_closes(monkeypatch, mode):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client

    calls = []
    closed = ThreadEvent()

    class Client:
        def __init__(self, config, approval_handler=None):
            assert callable(approval_handler)
            assert config.cwd.startswith("/")
            assert "features.hooks=false" in config.config_overrides
            assert "project_doc_max_bytes=0" in config.config_overrides

        def start(self):
            calls.append("start")

        def initialize(self):
            calls.append("initialize")

        def request(self, method, params, *, response_model):
            if method == "mcpServerStatus/list":
                if mode in {"disabled_mcp", "active_mcp"}:
                    return SimpleNamespace(data=[SimpleNamespace(
                        runtime_status=SimpleNamespace(value="disabled" if mode == "disabled_mcp" else "connected"),
                        tools={}, resources=[], resource_templates=[],
                    )], next_cursor=None)
                return SimpleNamespace(data=[], next_cursor=None)
            assert method == "config/read"
            return SimpleNamespace(config=SimpleNamespace(model_dump=lambda **k: {
                "features": {**dict.fromkeys(_DISABLED_FEATURES, False), "hooks": mode == "features"},
                "mcp_servers": {"external": {}},
                "project_doc_max_bytes": 0, "web_search": "disabled", "skills": {"include_instructions": False},
            }))

        def thread_start(self, params):
            assert params["ephemeral"] is True
            assert params["sandbox"] == "read-only"
            assert params["config"]["mcp_servers"]["external"]["enabled"] is False
            calls.append("thread")
            return SimpleNamespace(thread=SimpleNamespace(id="new-reflector-thread"), instruction_sources=[],
                                   sandbox=SimpleNamespace(root=SimpleNamespace(type="readOnly")))

        def close(self):
            calls.append("close")
            closed.set()

    def run(self, prompt, *, output_schema, effort):
        assert prompt == "inspect now" and output_schema == {"type": "object"}
        assert effort == "medium"
        calls.append("prompt-and-schema")
        if mode == "timeout":
            assert closed.wait(2), "Deadline did not close the runtime"
            raise RuntimeError("Runtime closed")
        return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="{}",
                               id="turn", items=[SimpleNamespace(type="commandExecution")] if mode == "tool" else [],
                               usage=None)

    monkeypatch.setattr(openai_codex.client, "CodexClient", Client)
    monkeypatch.setattr(sdk.Thread, "run", run)
    config = CodexReflectorConfig(model="test", timeout_seconds=0.1 if mode == "timeout" else 180)
    if mode not in {"success", "disabled_mcp"}:
        with pytest.raises(ReflectionError) as exc:
            _run_sdk("inspect now", {"type": "object"}, config)
        assert exc.value.code == ("timeout" if mode == "timeout" else "isolation")
        assert calls[-1] == "close"
        if mode in {"features", "active_mcp"}:
            assert "prompt-and-schema" not in calls
        return
    result = _run_sdk("inspect now", {"type": "object"}, config)
    assert result["thread_id"] == "new-reflector-thread"
    assert calls == ["start", "initialize", "thread", "prompt-and-schema", "close"]


def test_missing_optional_sdk_does_not_break_preparation(monkeypatch, agent):
    import builtins

    real_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("openai_codex"):
            raise ImportError("Not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    r = reflector()
    assert r.prepare([bundle()], agent)["manifest"]["session_ids"]
    with pytest.raises(ReflectionError) as exc:
        r.reflect([bundle()], agent)
    assert exc.value.code == "missing_sdk"


def test_rank_free_input_does_not_mutate_configuration(agent):
    from thearc.models.ranks import Ranks

    agent.skills["graphify"].ranks = Ranks(helpful=9876)
    agent.skills["graphify"].files["references/query.md"] = "# Query (harmful: 0, neutral: 0, helpful: 9876)\nText"
    before = agent.model_dump_json()
    prepared = reflector().prepare([bundle()], agent)
    assert "9876" not in prepared["prompt"]
    assert agent.model_dump_json() == before


def test_historical_ranks_echoed_in_sessions_and_hook_extras_are_hidden(agent):
    b = bundle()
    b.events[0].text = "# Query (harmful: 8761, neutral: 8762, helpful: 8763)\nUse the graph."
    b.events[1].arguments = {"config": {"ranks": {"helpful": 8764}, "instructions": "Use the graph."}}
    b.events[2].text = 'Read config: {"ranks": {"harmful": 8765}, "command": "graphify"}'
    b.events[3].text = "---\nranks:\n  harmful: 8766\n  neutral: 8767\n  helpful: 8768\n---\nTask complete."
    agent.hooks["before-bash"].extra["ranks"] = {"helpful": 8769}
    original = b.model_dump_json()
    prepared = reflector().prepare([b], agent)
    for number in range(8761, 8770):
        assert str(number) not in prepared["prompt"]
    assert "Use the graph." in prepared["prompt"]
    assert b.model_dump_json() == original


def test_resume_reuses_only_matching_validated_batches(agent, tmp_path):
    calls = []

    def sdk(*args):
        calls.append(1)
        return response()

    r = reflector(sdk_runner=sdk, artifact_dir=tmp_path, resume=True)
    first = r.reflect([bundle()], agent)
    assert r.reflect([bundle()], agent).id == first.id
    assert len(calls) == 1
    agent.skills["graphify"].instructions += " Changed"
    assert r.reflect([bundle()], agent).id != first.id
    assert len(calls) == 2
    assert (tmp_path / first.id / "report.md").exists()


def test_resume_ignores_partial_uncommitted_artifacts(agent, tmp_path):
    folder = tmp_path / "reflection-interrupted"
    folder.mkdir()
    (folder / "reflection.json").write_text('{"id":')
    (folder / "run.jsonl").write_text('{"partial":')
    result = reflector(sdk_runner=lambda *a: response(), artifact_dir=tmp_path, resume=True).reflect([bundle()], agent)
    assert result.id != folder.name


def test_resume_does_not_reuse_old_category_based_contract(agent, tmp_path):
    r = reflector(sdk_runner=lambda *a: response(), artifact_dir=tmp_path, resume=True)
    old = r.reflect([bundle()], agent)
    folder = tmp_path / old.id
    journal = folder / "run.jsonl"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    records[0]["payload"]["schema_version"] = "1"
    journal.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    (folder / "reflection.json").write_text('{"findings": [{"category": "success"}]}')
    assert r.reflect([bundle()], agent).id != old.id


@pytest.mark.skipif(os.environ.get("THEARC_TEST_CODEX_RUNTIME") != "1", reason="Opt-in local runtime probe")
def test_installed_runtime_isolation_without_inference(monkeypatch):
    """Start a real SDK/runtime thread, but replace the model turn entirely."""
    sdk = pytest.importorskip("openai_codex")
    called = []

    def no_inference(self, prompt, **kwargs):
        called.append(self.id)
        return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="{}",
                               id="no-inference", items=[], usage=None)

    monkeypatch.setattr(sdk.Thread, "run", no_inference)
    result = _run_sdk("unused", reflection_schema(), CodexReflectorConfig(model="test-model", timeout_seconds=20))
    assert result["turn_id"] == "no-inference" and called


def test_native_adapter_sessions_reach_reflector(tmp_path, agent):
    records = {
        "pi": ("session.jsonl", [
            {"type": "session", "id": "same-id", "version": 3},
            {"type": "message", "id": "u", "message": {"role": "user", "content": "Inspect graphify"}},
        ]),
        "claude": ("session.jsonl", [
            {"type": "user", "uuid": "u", "sessionId": "same-id",
             "message": {"role": "user", "content": "Inspect graphify"}},
        ]),
        "codex": ("rollout-session.jsonl", [
            {"type": "session_meta", "payload": {"id": "same-id"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Inspect graphify"}]}},
        ]),
        "antigravity": ("transcript.jsonl", [
            {"type": "USER_INPUT", "conversation_id": "same-id", "content": "Inspect graphify"},
        ]),
    }
    with SessionStore(tmp_path / "index.sqlite") as history:
        for harness, (filename, lines) in records.items():
            root = tmp_path / harness
            root.mkdir()
            (root / filename).write_text("\n".join(json.dumps(line) for line in lines) + "\n")
            history.register_source(SourceConfig(id=harness, harness=harness, root=root))
        history.sync()
        sessions = history.list_sessions()
        assert {s.harness for s in sessions} == set(records)
        evidence = history.snapshot(session_ids=[s.id for s in sessions])
        assert len(evidence.sessions) == 4
        assert len({s["id"] for s in evidence.sessions}) == 4
        assert {s["id"]: s["native_id"] for s in evidence.sessions} == {s.id: s.native_id for s in sessions}
        assert sum(s["native_id"] == "same-id" for s in evidence.sessions) >= 3
        assert {e["harness"] for e in evidence.events} == set(records)
        materializer = HistorySessionMaterializer(history)
        bundles = [materializer.materialize(s, AceConfig()) for s in sessions]
        assert all(b.events for b in bundles)
        prepared = reflector().prepare(bundles, agent)
        assert len(set(prepared["manifest"]["session_ids"])) == 4
        assert all("Inspect graphify" in json.dumps(s) for s in prepared["evidence"]["sessions"])
