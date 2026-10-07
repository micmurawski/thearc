import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from thearc.learning.curation.assessments import propagate_ranks
from thearc.models import MCP, Hook, ResourceTarget
from thearc.resources import (
    Curator,
    Reflection,
    ReflectionBaseModel,
    ReflectionError,
    ReflectionTools,
    Reflector,
    ResourceView,
)
from thearc.resources.schemas import _validator


def finding(tools, **changes):
    target = ResourceTarget(kind="skill", name="debug", section="Retry")
    return {"reasoning": "The unchanged retry repeated the error.", "error_identification": "Invalid tool inputs",
            "root_cause_analysis": "Error feedback was ignored", "correct_approach": "Inspect the error",
            "key_insight": "Change invalid inputs before retrying", "event_ids": ["e2"],
            "bullet_tags": [{"id": tools.resources.id_for(target), "tag": "harmful"}], **changes}


def test_repair_is_atomic_and_repeated_submission_is_idempotent(agent, history, tmp_path):
    path = tmp_path / "submission.json"
    tools = ReflectionTools(agent=agent, evidence=history, output=path)
    valid = finding(tools)
    bad = finding(tools, bullet_tags=[{"id": "missing", "tag": "helpful"}])
    result = tools.call("submit_reflections", {"reflections": [valid, bad]})
    assert not result["accepted"]
    assert result["errors"][0]["path"] == "reflections[1].bullet_tags[0].id"
    assert result["attempts_remaining"] == 2
    assert not path.exists()
    with pytest.raises(ReflectionError, match="No reflection list"):
        _ = tools.reflections
    request = {"reflections": [valid]}
    result = tools.call("submit_reflections", request)
    assert result["accepted"] and result["count"] == 1
    original = path.read_bytes()
    assert tools.call("submit_reflections", request) == result
    assert path.read_bytes() == original
    assert not tools.call("submit_reflections", {"reflections": []})["accepted"]
    bundle = json.loads(original)
    saved = tmp_path / "reflection.json"
    saved.write_text(json.dumps(bundle["reflections"][0]))
    assert Reflection.load(saved).output == valid
    assert tools.assessments[0].items[0].target.section == "Retry"
    # Neither caller input nor returned mutable models can change accepted output.
    valid["key_insight"] = "changed"
    tools.reflections[0].output.key_insight = "changed again"
    assert tools.reflections[0].output.key_insight == "Change invalid inputs before retrying"


@pytest.mark.parametrize("changes,code", [
    ({"event_ids": ["outside"]}, "unknown_event"),
    ({"event_ids": ["e2", "e2"]}, "invalid_evidence"),
    ({"bullet_tags": [{"id": "unknown", "tag": "invalid"}]}, "schema"),
    ({"event_ids": []}, "schema"),
])
def test_invalid_evidence_and_tags_are_recoverable(agent, history, changes, code):
    tools = ReflectionTools(agent=agent, evidence=history)
    result = tools.call("submit_reflections", {"reflections": [finding(tools, **changes)]})
    assert not result["accepted"] and result["errors"][0]["code"] == code
    assert tools.call("submit_reflections", {"reflections": [finding(tools)]})["accepted"]


def test_bounded_attempts_and_explicit_empty_success(agent, history):
    tools = ReflectionTools(agent=agent, evidence=history, max_submissions=1)
    assert not tools.call("submit_reflections", {"reflections": [{}]})["accepted"]
    assert tools.call("submit_reflections", {"reflections": []})["errors"][0]["code"] == "attempt_limit"
    empty = ReflectionTools(agent=agent, evidence=history)
    assert empty.call("submit_reflections", {"reflections": []})["accepted"]
    assert empty.reflections == empty.assessments == []
    empty.close()
    assert empty.call("submit_reflections", {"reflections": []})["errors"][0]["code"] == "closed"


def test_concurrent_transport_retries_accept_once(agent, history):
    tools = ReflectionTools(agent=agent, evidence=history)
    request = {"reflections": [finding(tools)]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: tools.call("submit_reflections", request), range(2)))
    assert results[0] == results[1]
    assert len(tools.assessments) == 1


def test_schema_local_refs_and_custom_resource_validation(agent, history):
    class Detail(ReflectionBaseModel):
        resource: ResourceTarget

    class CustomReflection(ReflectionBaseModel):
        detail: Detail

    tools = ReflectionTools(agent=agent, evidence=history, reflection_schema=CustomReflection)
    schema = tools.definitions[-1]["inputSchema"]
    valid = {"reflections": [{"detail": {"resource": {"kind": "skill", "name": "debug"}}}]}
    _validator(schema).validate(valid)
    invalid = {"reflections": [{"detail": {"resource": {"kind": "skill", "name": "missing"}}}]}
    assert not tools.call("submit_reflections", invalid)["accepted"]
    assert tools.call("submit_reflections", valid)["accepted"]
    assert tools.reflections[0].output.detail.resource.name == "debug"


def test_paged_views_and_rank_identity(agent, history):
    before = agent.model_dump()
    tools = ReflectionTools(agent=agent, evidence=history, page_chars=64)
    request = {"query": "Retry", "offset": 0, "limit": 1, "include_ranks": False}
    listed = tools.call("list_resources", request)
    target_id = listed["resources"][0]["id"]
    assert "content" not in listed["resources"][0]
    pages, offset = [], 0
    while offset is not None:
        page = tools.call("read_resource", {"id": target_id, "offset": offset, "include_ranks": False})
        assert len(page["content"]) <= 64
        pages.append(page["content"])
        offset = page["next_offset"]
    assert "Inspect errors first" in "".join(pages)
    assert "current_ranks" not in page
    assert tools.call("submit_reflections", {"reflections": [finding(tools)]})["accepted"]
    ranked, ledger = propagate_ranks(agent, tools.assessments, epoch_id="one")
    ranked_again, ledger_again = propagate_ranks(ranked, tools.assessments, epoch_id="two", ledger=ledger)
    view = ResourceView(ranked_again, ledger=ledger_again)
    page = view.read(target_id, include_ranks=True)
    assert page["current_ranks"]["harmful"] == page["lifetime_ranks"]["harmful"] == 1
    assert view.mapping == tools.resources.mapping
    assert view.revision == tools.resources.revision
    ranked_again.skills["debug"].instructions = "# Retry\nChanged content"
    updated = ResourceView(ranked_again, ledger=ledger_again).read(target_id, include_ranks=True)
    assert updated["current_ranks"]["harmful"] == 0
    assert updated["lifetime_ranks"]["harmful"] == 1
    assert agent.model_dump() == before


def test_resource_snapshot_and_ambiguous_headings(agent):
    view = ResourceView(agent)
    target_id = view.id_for(ResourceTarget(kind="skill", name="debug"))
    agent.skills["debug"].instructions = "# Retry\nOne\n# retry\nTwo"
    assert "Inspect errors first" in view.read(target_id)["content"]
    ambiguous = ResourceView(agent)
    assert all(item["section"] is None for item in ambiguous.mapping.values())


def test_hook_and_mcp_assessments_keep_ranks_out_of_executable_content(agent, history):
    agent.hooks["check"] = Hook(name="check", command="check-input")
    agent.mcps["lookup"] = MCP(name="lookup", command="lookup-service")
    tools = ReflectionTools(agent=agent, evidence=history)
    targets = [ResourceTarget(kind="hook", name="check"), ResourceTarget(kind="mcp", name="lookup")]
    tags = [{"id": tools.resources.id_for(target), "tag": "helpful"} for target in targets]
    assert tools.call("submit_reflections", {"reflections": [finding(tools, bullet_tags=tags)]})["accepted"]
    ranked, ledger = propagate_ranks(agent, tools.assessments, epoch_id="one")
    assert ranked.hooks["check"].ranks.helpful == 1
    assert ranked.hooks["check"].command == "check-input"
    assert ranked.mcps["lookup"] == agent.mcps["lookup"]
    page = ResourceView(ranked, ledger=ledger).read(tags[1]["id"], include_ranks=True)
    assert page["current_ranks"]["helpful"] == 1


def test_curator_receives_section_ids_and_ranks(agent, history):
    tools = ReflectionTools(agent=agent, evidence=history)
    data = finding(tools)
    assert tools.call("submit_reflections", {"reflections": [data]})["accepted"]

    def curate(prompt, schema, config):
        ranks = json.loads(prompt.split("RESOURCE RANKS AND EDIT PERMISSIONS:\n")[1].split("\n\n")[0])
        section = next(item for item in ranks if item["id"] == data["bullet_tags"][0]["id"])
        assert section["current_ranks"]["harmful"] == 1
        return {"status": "completed", "final_response": '{"summary":"Retain guidance", "changes":[]}'}

    result = Curator(implementation="custom", model="fixture", runner=curate).curate(
        "Improve the agent", tools.assessments, agent=agent,
    )
    assert result.agent.rank_for(ResourceTarget(kind="skill", name="debug", section="Retry")).harmful == 1


def test_schema_metadata_does_not_embed_agent_or_history(agent, history):
    agent.skills["debug"].instructions = "LARGE_RESOURCE " * 100_000
    tools = ReflectionTools(agent=agent, evidence=history)
    reflector = Reflector(implementation="codex", model="fixture", max_input_chars=2000)
    prepared = reflector.prepare("Assess the loaded session", tools=tools)
    assert len(prepared["prompt"]) < 2000
    assert "LARGE_RESOURCE" not in json.dumps(tools.definitions)
    assert "SyntaxError" not in prepared["prompt"]


def test_submission_never_overwrites_an_existing_artifact(agent, history, tmp_path):
    output = tmp_path / "submission.json"
    tools = ReflectionTools(agent=agent, evidence=history, output=output)
    output.write_text("another job won the race")
    with pytest.raises(FileExistsError):
        tools.call("submit_reflections", {"reflections": []})
    assert not tools.accepted
    assert output.read_text() == "another job won the race"


def test_tool_reflector_uses_submission_not_final_text(agent, history, tmp_path, monkeypatch):
    tools = ReflectionTools(agent=agent, evidence=history)

    def run(prompt, schema, config, **options):
        assert "Inspect errors first" not in prompt
        assert "JSON Schema" not in prompt and schema is None
        assert options["scoped_tools"] is tools
        assert tools.call("submit_reflections", {"reflections": [finding(tools)]})["accepted"]
        return {"status": "completed", "final_response": "Done", "thread_id": "reflector-session"}

    monkeypatch.setattr("thearc.learning.reflection.providers.codex._run_isolated_sdk", run)
    reflector = Reflector(implementation="codex", model="fixture", artifact_dir=tmp_path)
    reflections = reflector.reflect("Reflect on provided evidence", tools=tools)
    assert len(reflections) == 1
    assert reflections[0].output.event_ids == ["e2"]
    saved = next(tmp_path.rglob("reflection.json"))
    assert Reflection.load(saved).execution["thread_id"] == "reflector-session"


def test_final_text_without_submission_fails(agent, history, monkeypatch):
    tools = ReflectionTools(agent=agent, evidence=history)
    monkeypatch.setattr("thearc.learning.reflection.providers.codex._run_isolated_sdk", lambda *a, **k: {
        "status": "completed", "final_response": '{"reflections": []}',
    })
    with pytest.raises(ReflectionError, match="No reflection list"):
        Reflector(implementation="codex", model="fixture").reflect("Reflect", tools=tools)


def test_trajectory_handoff_preserves_context_and_scope(agent, history, monkeypatch):
    trajectory = history.split_into_trajectories()[0]
    tools = ReflectionTools(agent=agent, evidence=trajectory)

    def run(prompt, schema, config, **options):
        assert options["history_items"] and "SyntaxError" not in prompt
        options["on_import"]({"session_id": "fork"})
        tools.call("submit_reflections", {"reflections": [finding(tools)]})
        return {"status": "completed", "thread_id": "fork"}

    monkeypatch.setattr("thearc.learning.reflection.providers.codex._run_isolated_sdk", run)
    reflector = Reflector(implementation="codex", model="fixture")
    result = reflector.handoff("Reflect", trajectory, tools=tools)
    assert result[0].provenance["mode"] == "import"
    assert result[0].history is trajectory
    with pytest.raises(ValueError, match="match the handoff"):
        reflector.handoff("Reflect", trajectory, tools=ReflectionTools(agent=agent, evidence=history))


def test_prompt_lists_every_resource_id(agent, history):
    tools = ReflectionTools(agent=agent, evidence=history)
    prompt = Reflector(implementation="codex", model="m").prepare("Reflect", tools=tools)["prompt"]
    assert tools.resources.mapping
    for resource_id, target in tools.resources.mapping.items():
        assert f"{resource_id} | {target['kind']} | {target['name']}" in prompt
    assert f"AGENT RESOURCES ({len(tools.resources.mapping)} IDs" in tools.instructions


def test_large_resource_index_keeps_whole_resources(agent, history, monkeypatch):
    monkeypatch.setattr(ReflectionTools, "max_index_chars", 10)
    tools = ReflectionTools(agent=agent, evidence=history)
    sections = [k for k, t in tools.resources.mapping.items() if t["section"]]
    whole = [k for k, t in tools.resources.mapping.items() if not t["section"]]
    assert sections and all(k in tools.instructions for k in whole)
    assert not any(k in tools.instructions for k in sections)
    assert f"{len(sections)} section IDs omitted" in tools.instructions


def test_resource_view_uses_model_ids_and_ledger_ranks(agent):
    view = ResourceView(agent)
    assert set(view.mapping) == {target.id for target, _ in agent.without_ranks().iter_targets()}
    assert view.catalog_markdown(include_ranks=True).count("ranks: (") == len(view.mapping)


def referenced(**changes):
    return {"reasoning": "Retried without reading the error.", "error_identification": "Blind retry",
            "root_cause_analysis": "The skill does not say to read errors first", "correct_approach": "Read errors",
            "key_insight": "Inspect errors before retrying",
            "resource_refs": [{"ref": "skills/debug/SKILL.md#L1-L3", "tag": "harmful"}], **changes}


def test_file_references_rate_sections_without_event_ids(agent, history):
    from thearc.resources import ReferencedReflection

    tools = ReflectionTools(agent=agent, evidence=history, reflection_schema=ReferencedReflection)
    names = [item["name"] for item in tools.definitions]
    assert names == ["list_files", "read_file", "submit_reflections"]
    retry = ResourceTarget(kind="skill", name="debug", section="Retry")
    assert "skills/debug/SKILL.md (3 lines)" in tools.instructions
    assert f"L1-L3 Retry [SectionId: {retry.id}]" in tools.instructions
    assert tools.call("read_file", {"path": "skills/debug/SKILL.md", "start_line": 1, "end_line": 1})[
        "content"] == "1 | # Retry"
    crossing = referenced(resource_refs=[{"ref": "skills/debug/SKILL.md#L9", "tag": "harmful"}])
    rejected = tools.call("submit_reflections", {"reflections": [crossing]})
    assert rejected["errors"][0]["path"] == "reflections[0].resource_refs[0].ref"
    receipt = tools.call("submit_reflections", {"reflections": [referenced()]})
    assert receipt["ranked"][0] | {"reflection_id": None} == {
        "reflection_id": None, "ref": "skills/debug/SKILL.md#L1-L3", "tag": "harmful",
        "section_id": retry.id, "section": "Retry"}
    item = tools.assessments[0].items[0]
    assert item.target == ResourceTarget(kind="skill", name="debug", section="Retry")
    assert item.reference == "skills/debug/SKILL.md#L1-L3"
    assert [e.event_id for e in item.evidence] == [e.id for e in history.selected_events]
    ranked, ledger = agent.propagate_ranks(tools.assessments, epoch_id="e1")
    assert ranked.rank_for(item.target).harmful == agent.rank_for(item.target).harmful + 1
    assert ledger.assessments[0].reference == "skills/debug/SKILL.md#L1-L3"
    assert ledger.assessments[0].target_id == retry.id
    assert f"# Retry [SectionId: {retry.id}, ranks: (harmful: 1," in ranked.to_labeled_markdown(include_ranks=True)
    again, _ = ranked.propagate_ranks(tools.assessments, epoch_id="e1", ledger=ledger)
    assert again.rank_for(item.target) == ranked.rank_for(item.target)  # same report is not counted twice
