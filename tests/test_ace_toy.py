from functools import partial

import pytest

from thearc import ContextDocument, Hook, MetaAgent, ResourceTarget, Skill
from thearc.learning import (
    AceConfig,
    AcePipeline,
    ChangeJournal,
    CheckpointStore,
    RunJournal,
    Session,
    SessionBundle,
    ToyCurator,
    ToyReflector,
    apply_curations,
    run_toy_flow,
)
from thearc.learning.models import Event, SourceReference
from thearc.models import Ranks


def event(tmp_path, status):
    return Event(
        id=f"event-{status}",
        source_id="demo",
        harness="pi",
        session_id="session",
        run_id="run",
        kind="tool_result",
        status=status,
        text=status,
        reference=SourceReference(path=tmp_path / "session.jsonl", generation=0, byte_offset=0, byte_length=1, line=1),
    )


def test_toy_reflector_hides_ranks_and_curator_uses_them(tmp_path):
    target = MetaAgent(
        name="demo",
        skills=[Skill(name="demo", description="Demo", instructions="Do the thing.", ranks=Ranks(harmful=5))],
        context=[
            ContextDocument(
                filename="AGENTS.md",
                content="# Rules (harmful: 5, neutral: 0, helpful: 0)\n\nDo the thing.\n",
            )
        ],
        hooks=[Hook(name="guard", ranks=Ranks(harmful=5))],
    )

    reflection = ToyReflector().reflect(
        [
            SessionBundle(
                session=Session(id="session", source_id="demo", harness="pi", native_id="native"),
                events=[event(tmp_path, "success")],
            )
        ],
        target,
    )
    assert all("ranks" not in proposal for proposal in reflection.rank_proposals)
    assert reflection.rank_proposals

    curator = ToyCurator()
    curation = curator.curate([reflection], target)
    assert curator.seen_ranks[0]["skill:demo:None"]["harmful"] == 5
    assert len(curation) == 3

    journal = ChangeJournal(tmp_path / "changes.jsonl")
    result = apply_curations(target.model_copy(deep=True), curation, partial(journal.record, "run-1"))
    assert len(result.applied_curation_ids) == 3
    assert len(journal.entries()) == 3
    assert result.agent.skills["demo"].ranks.helpful == 1
    assert result.agent.rank_for(ResourceTarget(kind="context", name="AGENTS.md", section="Rules")).helpful == 1
    assert result.agent.hooks["guard"].ranks.helpful == 1
    assert all(entry.reason.summary for entry in journal.entries())


def test_skill_reference_is_ranked_and_journaled_independently(tmp_path):
    target = MetaAgent(
        name="demo",
        skills=[
            Skill(
                name="demo",
                description="Demo",
                instructions="Do it.",
                files={"references/hooks.md": "# Hooks\n\nUse the hook.\n"},
            )
        ],
    )

    reflection = ToyReflector().reflect(
        [
            SessionBundle(
                session=Session(id="session", source_id="demo", harness="pi", native_id="native"),
                events=[event(tmp_path, "success")],
            )
        ],
        target,
    )
    proposal = next(item for item in reflection.rank_proposals if item["target"]["kind"] == "skill_file")
    assert proposal["target"]["name"] == "demo/references/hooks.md"

    journal = ChangeJournal(tmp_path / "changes.jsonl")
    curation = ToyCurator().curate([reflection], target)
    result = apply_curations(target.model_copy(deep=True), curation, partial(journal.record, "run-1"))

    assert result.agent.rank_for(ResourceTarget(kind="skill_file", name="demo/references/hooks.md")).helpful == 1
    assert any(entry.target.kind == "skill_file" for entry in journal.entries())


def test_toy_runner_uses_flow_graph(tmp_path):
    target = MetaAgent(
        name="demo",
        skills=[Skill(name="demo", description="Demo", instructions="Do it.")],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nDo it.\n")],
        hooks=[Hook(name="guard")],
    )
    session = Session(id="session", source_id="demo", harness="pi", native_id="native")

    class Selector:
        def select(self, query=None):
            return [session]

    class Materializer:
        def materialize(self, current, config):
            return SessionBundle(session=current, events=[event(tmp_path, "success")])

    result = run_toy_flow(
        selector=Selector(),
        materializer=Materializer(),
        agent=target,
        journal=ChangeJournal(tmp_path / "flow-journal.jsonl"),
        viz_path=tmp_path / "ace-flow.html",
        run_journal=RunJournal(tmp_path / "run.jsonl"),
        checkpoint_path=tmp_path / "checkpoint.json",
    )
    assert result.agent.name == target.name
    assert result.agent.skills["demo"].ranks.helpful == 1
    assert result.agent.hooks["guard"].ranks.helpful == 1
    assert result.agent.rank_for(ResourceTarget(kind="context", name="AGENTS.md", section="Rules")).helpful == 1
    assert len(ChangeJournal(tmp_path / "flow-journal.jsonl").entries()) == 3
    assert "Query sessions" in (tmp_path / "ace-flow.html").read_text(encoding="utf-8")
    assert [record.event for record in RunJournal(tmp_path / "run.jsonl").records()] == [
        "adaptation_started",
        "adaptation_completed",
    ]
    assert CheckpointStore(tmp_path / "checkpoint.json").load().status == "completed"


@pytest.mark.parametrize("runner", ["pipeline", "flow"])
@pytest.mark.parametrize("session_count", [0, 3])
def test_metaagent_result_accumulates_batches_without_modifying_input(tmp_path, runner, session_count):
    original = MetaAgent(skills=[Skill(
        name="demo", ranks=Ranks(helpful=4), files={"references/query.md": "# Query\n\nSearch first."},
    )])
    before = original.model_dump()

    class Selector:
        def select(self, query=None):
            return [Session(id=f"s{i}", source_id="demo", harness="pi", native_id=str(i))
                    for i in range(session_count)]

    class Materializer:
        def materialize(self, current, config):
            return SessionBundle(session=current, events=[event(tmp_path, "success")])

    config = AceConfig(sessions_per_reflection=1, reflections_per_curation=1)
    journal = ChangeJournal(tmp_path / "changes.jsonl")
    if runner == "pipeline":
        result = AcePipeline(
            Selector(), Materializer(), ToyReflector(), ToyCurator(), config,
            on_change=partial(journal.record, "test"),
        ).run(original).update
    else:
        result = run_toy_flow(
            selector=Selector(), materializer=Materializer(), agent=original,
            journal=journal, config=config,
        )
    assert original.model_dump() == before
    assert result.agent.skills["demo"].ranks.helpful == 4 + session_count
    reference = ResourceTarget(kind="skill_file", name="demo/references/query.md")
    assert result.agent.rank_for(reference).helpful == session_count
    assert len(result.applied_curation_ids) == 2 * session_count
    assert not result.rejected_curation_ids
    assert len(journal.entries()) == 2 * session_count
    assert MetaAgent.model_validate_json(result.agent.model_dump_json()) == result.agent
