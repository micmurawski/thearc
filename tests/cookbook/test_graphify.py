"""Graphify recipes exercise real validation and curation with simulated model responses."""

import json
import re
import threading

import pytest

from cookbook import graphify_evidence, graphify_handoff, graphify_parallel
from cookbook.graphify_inputs import load_inputs, load_runs
from cookbook.graphify_patterns import ToolRetryAfterFailure
from thearc import MetaAgent, Skill
from thearc.learning.sessions.models import SearchFilters, SourceConfig
from thearc.learning.sessions.patterns import StubbornToolLoop
from thearc.resources import Curator, MergeResult, OutputValidationError, ReflectionError, Reflector, SessionStore


@pytest.fixture
def captured_runs(tmp_path):
    batch = tmp_path / "batch"
    sessions = batch / "sessions"
    sessions.mkdir(parents=True)
    agent = MetaAgent(name="recorded-agent", skills=[Skill(name="graphify", instructions="Inspect the graph.")])
    for run_id in ("a", "b"):
        run = batch / "runs" / run_id
        run.mkdir(parents=True)
        (run / "agent-before.json").write_text(agent.model_dump_json())
        # Deliberately stale absolute paths: the loader uses the local copied transcript.
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "thread_id": run_id,
                    "transcript": f"/old/machine/rollout-{run_id}.jsonl",
                }
            )
        )
        records = [{"type": "session_meta", "payload": {"id": run_id}}]
        for role, text in (("user", f"graphify task-{run_id}"), ("assistant", "Graph query located routing.py.")):
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
        (sessions / f"rollout-{run_id}.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    return batch


@pytest.fixture
def pattern_index(captured_runs, tmp_path):
    source = captured_runs / "sessions"
    with (source / "rollout-a.jsonl").open("a") as stream:
        payloads = [
            {"type": "function_call", "call_id": "first", "name": "shell", "arguments": '{"cmd":"graphify bad"}'},
            {"type": "function_call_output", "call_id": "first", "output": '{"exit_code":1,"output":"bad query"}'},
            {"type": "function_call", "call_id": "retry", "name": "shell", "arguments": '{"cmd":"graphify fixed"}'},
            {"type": "function_call_output", "call_id": "retry", "output": '{"exit_code":0,"output":"found routes"}'},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OUTSIDE MATCH"}]},
        ]
        stream.write("".join(json.dumps({"type": "response_item", "payload": p}) + "\n" for p in payloads))
    index = tmp_path / "patterns.sqlite"
    with SessionStore(index) as store:
        store.ingest(SourceConfig(id="graphify-runs", harness="codex", root=source))
    return index


def test_loader_detaches_histories_and_checks_baselines(captured_runs):
    agent, histories = load_runs(captured_runs, ["a", "b"])
    assert agent.name == "recorded-agent"
    assert [t.selected_events[0].text for t in histories] == ["graphify task-a", "graphify task-b"]
    assert "routing.py" in histories[0].to_markdown()
    path = captured_runs / "runs/b/agent-before.json"
    changed = MetaAgent.model_validate_json(path.read_text())
    changed.skills["graphify"].instructions = "Different baseline"
    path.write_text(changed.model_dump_json())
    with pytest.raises(ValueError, match="identical recorded baselines"):
        load_runs(captured_runs, ["a", "b"])


@pytest.fixture
def mixed_inputs(captured_runs):
    claude = captured_runs / "claude"
    claude.mkdir()
    # Same native ID as the Codex run; source IDs must keep these separate.
    records = [
        {"type": "user", "sessionId": "a", "message": {"role": "user", "content": "Claude graph task"}},
        {
            "type": "assistant",
            "sessionId": "a",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "graphify query routes"}},
                ],
            },
        },
        {
            "type": "user",
            "sessionId": "a",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call", "content": "Located routing.py"},
                ],
            },
        },
        {"type": "assistant", "sessionId": "a", "message": {"role": "assistant", "content": "Done"}},
    ]
    (claude / "a.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    original = MetaAgent(name="claude-producer", skills=[Skill(name="graphify", instructions="Different guidance")])
    (captured_runs / "claude-agent.json").write_text(original.model_dump_json())
    path = captured_runs / "inputs.json"
    path.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "source": {"id": "codex-source", "harness": "codex", "root": "sessions"},
                        "native_session_id": "a",
                        "agent": "runs/a/agent-before.json",
                    },
                    {
                        "source": {"id": "claude-source", "harness": "claude", "root": "claude"},
                        "native_session_id": "a",
                        "agent": "claude-agent.json",
                    },
                ]
            }
        )
    )
    return path


def test_mixed_harnesses_keep_producers_and_tool_activity(mixed_inputs):
    with pytest.raises(ValueError, match="explicit target"):
        load_inputs(mixed_inputs)
    target, _ = load_runs(mixed_inputs.parent, ["a"])
    inputs = load_inputs(mixed_inputs, target=target)
    assert inputs.agent == target
    assert [t.session.metadata.harness for t in inputs.histories] == ["codex", "claude"]
    assert inputs.histories[0].session.id != inputs.histories[1].session.id
    assert inputs.source_agents[1].name == "claude-producer"
    events = inputs.histories[1].selected_events
    assert [e.kind for e in events] == ["message", "tool_call", "tool_result", "message"]
    assert events[1].call_id == events[2].call_id
    # Codex handoff's translation retains calls/results originating in Claude.
    items = [json.loads(line)["payload"] for line in inputs.histories[1].to_codex().text.splitlines()]
    assert [item["type"] for item in items] == ["message", "function_call", "function_call_output", "message"]


def test_saved_session_input_needs_no_original_source(captured_runs):
    agent, histories = load_runs(captured_runs, ["a"])
    histories[0].session.save(captured_runs / "session.json")
    path = captured_runs / "saved-inputs.json"
    path.write_text(json.dumps({"sessions": [{"artifact": "session.json", "agent": "runs/a/agent-before.json"}]}))
    inputs = load_inputs(path)
    assert inputs.agent == agent
    assert inputs.histories[0].to_jsonl() == histories[0].to_jsonl()


def test_custom_runtime_is_used_for_candidates_and_merge_review(captured_runs, tmp_path):
    agent, histories = load_runs(captured_runs, ["a", "b"])
    stages = []

    def runner(prompt, schema, config):
        stages.append(schema["title"])
        assert config.model == "other-harness-model"
        if schema["title"] == "ReflectionSchema":
            output = assessment({"prompt": prompt})
        elif schema["title"] == "CurationReport":
            output = {"summary": "Retain", "changes": []}
        else:
            assert schema["title"] == "MergeReview"
            output = {"issues": [], "limitations": []}
        return {"status": "completed", "final_response": json.dumps(output)}

    result = graphify_parallel.adapt(
        agent,
        histories,
        implementation="custom",
        runner=runner,
        model="other-harness-model",
        output=tmp_path / "parallel",
    )
    assert not result.unresolved
    assert stages.count("ReflectionSchema") == stages.count("CurationReport") == 2
    assert stages[-1] == "MergeReview"


def test_index_filters_sessions_and_reflects_only_matched_windows(captured_runs, pattern_index, tmp_path, monkeypatch):
    from thearc.learning.sessions.store import SessionStore as Index

    agent, _ = load_runs(captured_runs, ["a"])
    loaded = []
    original = Index.iter_events

    def iter_events(self, filters=None):
        loaded.extend(filters.session_ids)
        yield from original(self, filters)

    monkeypatch.setattr(Index, "iter_events", iter_events)
    seen = []

    def runner(prompt, schema, config):
        seen.append(prompt)
        if schema["title"] == "ReflectionSchema":
            assert "graphify bad" in prompt and "graphify fixed" in prompt and "bad query" in prompt
            assert "OUTSIDE MATCH" not in prompt and "graphify task-b" not in prompt
            assert "Match 1: stubborn_tool_loop" in prompt
            first = assessment({"prompt": prompt})
            second = {**first, "summary": "A separate reflection on recovery"}
            output = {"reflections": [first, second]}
        else:
            findings, _ = json.JSONDecoder().raw_decode(prompt.split("REFLECTIONS:\n", 1)[1])
            assert findings[0]["items"] == []  # Producer identity is unknown: no historical votes for the target.
            reflected = json.loads(findings[0]["summary"])["reflections"]
            assert len(reflected) == 2 and reflected[1]["summary"] == "A separate reflection on recovery"
            output = {"summary": "Retain", "changes": []}
        return {"status": "completed", "final_response": json.dumps(output)}

    with SessionStore(pattern_index) as store:
        session_id = next(iter(store.search(status="failure"))).session.id
        result = graphify_evidence.adapt(
            agent,
            store,
            filters=SearchFilters(statuses=["failure"]),
            pattern=StubbornToolLoop(),
            reflector=Reflector(implementation="custom", model="fixture", runner=runner),
            curator=Curator(implementation="custom", model="fixture", runner=runner),
            output=tmp_path / "matched",
        )
    assert result is not None and len(seen) == 2
    assert loaded == [session_id]  # Noncandidate sessions were never decoded.
    assert len(re.findall(r'### Event "([^"]+)"', seen[0])) == 3
    assert (tmp_path / "matched/HEAD.json").is_file()


def test_no_pattern_matches_skips_all_inference(captured_runs, pattern_index, tmp_path):
    agent, _ = load_runs(captured_runs, ["a"])

    def runner(*args):
        pytest.fail("An empty match set must not invoke a model")

    with SessionStore(pattern_index) as store:
        result = graphify_evidence.adapt(
            agent,
            store,
            filters=SearchFilters(source_ids=["absent"]),
            pattern=StubbornToolLoop(),
            reflector=Reflector(implementation="custom", model="fixture", runner=runner),
            curator=Curator(implementation="custom", model="fixture", runner=runner),
            output=tmp_path / "empty",
        )
    assert result is None
    assert not (tmp_path / "empty").exists()


@pytest.mark.parametrize("role,expected", [("assistant", 1), ("user", 0)])
def test_retry_pattern_preserves_commentary_but_stops_at_a_new_prompt(pattern_index, role, expected):
    with SessionStore(pattern_index) as store:
        session = next(iter(store.search(status="failure"))).session
    events = session.events
    first, result, retry = events[-5:-2]
    intervening = next(event for event in events if event.kind == "message" and event.role == role)
    window = [first, result, intervening, retry]
    matches = ToolRetryAfterFailure().find_matches(window)
    assert len(matches) == expected
    if matches:
        assert matches[0].events == tuple(window)


@pytest.mark.parametrize("bad_output", ["event", "resource", "schema"])
def test_matched_evidence_is_validated_before_curation(captured_runs, pattern_index, tmp_path, bad_output):
    agent, _ = load_runs(captured_runs, ["a"])
    calls = []

    def runner(prompt, schema, config):
        calls.append(schema)
        assert schema == graphify_evidence.ReflectionSchema.model_json_schema()
        output = assessment({"prompt": prompt}, event_id="outside-window" if bad_output == "event" else None)
        if bad_output == "resource":
            output["findings"][0]["resource"]["name"] = "absent"
        if bad_output == "schema":
            output.pop("summary")
        return {
            "status": "completed",
            "final_response": json.dumps(
                {
                    "reflections": [assessment({"prompt": prompt}), output],
                }
            ),
        }

    with (
        SessionStore(pattern_index) as store,
        pytest.raises(
            ValueError if bad_output == "event" else OutputValidationError,
        ),
    ):
        graphify_evidence.adapt(
            agent,
            store,
            filters=SearchFilters(statuses=["failure"]),
            pattern=StubbornToolLoop(),
            reflector=Reflector(implementation="custom", model="fixture", runner=runner),
            curator=Curator(implementation="custom", model="fixture", runner=runner),
            output=tmp_path / "invalid",
        )
    assert len(calls) == 1
    assert not (tmp_path / "invalid").exists()


def test_empty_reflection_list_skips_curation(captured_runs, pattern_index, tmp_path):
    agent, _ = load_runs(captured_runs, ["a"])

    def no_curation(*args):
        pytest.fail("No findings should not trigger curation")

    with SessionStore(pattern_index) as store:
        result = graphify_evidence.adapt(
            agent,
            store,
            filters=SearchFilters(statuses=["failure"]),
            pattern=StubbornToolLoop(),
            reflector=Reflector(
                implementation="custom",
                model="fixture",
                runner=lambda *args: {
                    "status": "completed",
                    "final_response": '{"reflections":[]}',
                },
            ),
            curator=Curator(implementation="custom", model="fixture", runner=no_curation),
            output=tmp_path / "no-findings",
        )
    assert result is None and not (tmp_path / "no-findings").exists()


def test_handoff_does_not_silently_fall_back_to_prompt_evidence(captured_runs, tmp_path):
    agent, histories = load_runs(captured_runs, ["a"])
    with pytest.raises(ReflectionError, match="history import"):
        graphify_handoff.adapt(
            agent, histories[0], implementation="claude", model="fixture", output=tmp_path / "unsupported"
        )
    assert not (tmp_path / "unsupported").exists()


def assessment(prepared, *, event_id=None):
    prompt = prepared["prompt"]
    event_id = event_id or re.search(r'### Event "([^"]+)"', prompt)[1]
    return {
        "summary": "task-a" if "task-a" in prompt else "task-b",
        "limitations": [],
        "findings": [
            {
                "resource": {"kind": "skill", "name": "graphify"},
                "rating": "helpful",
                "event_ids": [event_id],
                "reason": "Graph query located the implementation; retain targeted queries.",
            }
        ],
    }


def test_handoff_inherits_evidence_and_curates_custom_schema(captured_runs, tmp_path, monkeypatch):
    agent, histories = load_runs(captured_runs, ["a"])
    before = agent.model_dump()
    calls = []

    def run(self, prepared, receipt):
        calls.append(prepared)
        if prepared["schema"]["title"] == "ReflectionSchema":
            assert "graphify task-a" not in prepared["prompt"]
            assert "graphify task-a" in json.dumps(prepared["history_items"])
            assert prepared["schema"] == graphify_handoff.ReflectionSchema.model_json_schema()
            receipt({"session_id": "reflector-destination"})
            output = {
                "summary": "Useful query",
                "findings": [
                    {
                        "resource": {"kind": "skill", "name": "graphify"},
                        "observation": "Located routing.py",
                        "evidence_excerpt": "Graph query located routing.py.",
                        "recommendation": "Retain targeted queries",
                    }
                ],
                "limitations": [],
            }
            return {"status": "completed", "thread_id": "reflector-destination", "final_response": json.dumps(output)}
        assert "Retain targeted queries" in prepared["prompt"]
        assert "arc_evidence" in prepared["prompt"]
        return {"status": "completed", "final_response": '{"summary":"Retain guidance", "changes":[]}'}

    monkeypatch.setattr(Reflector, "_run", run)
    result = graphify_handoff.adapt(agent, histories[0], model="fixture", output=tmp_path / "adapt")
    assert len(calls) == 2
    assert result.agent.without_ranks() == agent.without_ranks() and agent.model_dump() == before
    assert (tmp_path / "adapt/curation/HEAD.json").is_file()


def test_parallel_candidates_resolve_conflict_and_preserve_baseline(captured_runs, tmp_path, monkeypatch):
    agent, histories = load_runs(captured_runs, ["a", "b"])
    before = agent.model_dump()
    barrier = threading.Barrier(2)
    stages = []

    def run(self, prepared, receipt):
        title = prepared["schema"]["title"]
        stages.append(title)
        prompt = prepared["prompt"]
        if title == "ReflectionSchema":
            barrier.wait(timeout=10)  # Both reflection workers must run concurrently.
            assert prepared["schema"] == graphify_parallel.ReflectionSchema.model_json_schema()
            output = assessment(prepared)
        elif title == "CurationReport":
            findings, _ = json.JSONDecoder().raw_decode(prompt.split("REFLECTIONS:\n", 1)[1])
            output = {
                "summary": "Clarify query guidance",
                "changes": [
                    {
                        "id": "edit",
                        "target": {"kind": "skill", "name": "graphify"},
                        "operation": "EDIT",
                        "value": {"instructions": "Query first for " + findings[0]["summary"]},
                        "reflection_ids": [findings[0]["id"]],
                        "rationale": "Supported by the observed query",
                    }
                ],
            }
        elif title == "MergeReport":
            request, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"base":') :])
            output = {
                "limitations": [],
                "resolutions": [
                    {
                        "conflict_id": conflict["id"],
                        "action": "select",
                        "changes": [],
                        "selected_change_ids": [conflict["proposals"][0]["id"]],
                        "rationale": "Select one supported query policy",
                    }
                    for conflict in request["conflicts"]
                ],
            }
        else:
            assert title == "MergeReview"
            output = {"issues": [], "limitations": []}
        return {"status": "completed", "final_response": json.dumps(output)}

    monkeypatch.setattr(Reflector, "_run", run)
    result = graphify_parallel.adapt(agent, histories, model="fixture", output=tmp_path / "parallel")
    assert stages.count("ReflectionSchema") == stages.count("CurationReport") == 2
    assert stages[-2:] == ["MergeReport", "MergeReview"]
    assert result.conflicts and not result.unresolved
    assert "Query first" in result.agent.skills["graphify"].instructions
    assert agent.model_dump() == before
    assert len(list((tmp_path / "parallel").glob("candidate-*/curation/HEAD.json"))) == 2
    assert MergeResult.load(tmp_path / "parallel/merge").agent == result.agent
