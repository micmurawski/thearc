import json

import pytest

from thearc.history import (
    ChangeJournal,
    ChangeReason,
    CheckpointStore,
    FileResourceStore,
    Playbook,
    RunJournal,
    Session,
    SessionBundle,
    ToyCurator,
    ToyReflector,
    ToyUpdater,
    run_toy_flow,
)
from thearc.history.models import Event, SourceReference
from thearc.models import Hook, Ranks


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
    skill = tmp_path / "skill.md"
    skill.write_text(
        "---\nname: demo\ndescription: Demo\nranks:\n  harmful: 5\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    context = tmp_path / "AGENTS.md"
    context.write_text("# Rules (harmful: 5, neutral: 0, helpful: 0)\n\nDo the thing.\n", encoding="utf-8")
    hook = tmp_path / "hook.json"
    hook.write_text(json.dumps(Hook(name="guard", ranks=Ranks(harmful=5)).to_dict()), encoding="utf-8")
    store = FileResourceStore({"demo": skill}, {"AGENTS.md": context}, {"guard": hook})
    target = store.load_context()

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

    curator = ToyCurator(target)
    curation = curator.curate([reflection], Playbook(id="playbook"))
    assert curator.seen_ranks[0]["skill:demo:None"]["harmful"] == 5
    assert len(curation.operations) == 3

    journal = ChangeJournal(tmp_path / "changes.jsonl")
    result = ToyUpdater(store, journal, run_id="run-1").update(Playbook(id="playbook"), [curation])
    assert len(result.applied_curation_ids) == 3
    assert len(journal.entries()) == 3
    assert "helpful: 1" in skill.read_text(encoding="utf-8")
    assert "helpful: 1" in context.read_text(encoding="utf-8")
    assert json.loads(hook.read_text(encoding="utf-8"))["ranks"]["helpful"] == 1
    assert all(entry.reason.summary for entry in journal.entries())


def test_mutation_requires_reason(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("---\nname: demo\ndescription: Demo\n---\n\nDo it.\n", encoding="utf-8")
    store = FileResourceStore({"demo": skill}, {}, {})
    with pytest.raises(ValueError):
        store.update_skill("demo", Ranks(helpful=1), reason=ChangeReason(summary=" "))


def test_toy_runner_uses_flow_graph(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("---\nname: demo\ndescription: Demo\n---\n\nDo it.\n", encoding="utf-8")
    context = tmp_path / "AGENTS.md"
    context.write_text("# Rules\n\nDo it.\n", encoding="utf-8")
    hook = tmp_path / "hook.json"
    hook.write_text(json.dumps(Hook(name="guard").to_dict()), encoding="utf-8")
    store = FileResourceStore({"demo": skill}, {"AGENTS.md": context}, {"guard": hook})
    target = store.load_context()
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
        target=target,
        store=store,
        journal=ChangeJournal(tmp_path / "flow-journal.jsonl"),
        playbook=Playbook(id="playbook"),
        viz_path=tmp_path / "ace-flow.html",
        run_journal=RunJournal(tmp_path / "run.jsonl"),
        checkpoint_path=tmp_path / "checkpoint.json",
    )
    assert result.new_revision == 1
    assert len(ChangeJournal(tmp_path / "flow-journal.jsonl").entries()) == 3
    assert "Query sessions" in (tmp_path / "ace-flow.html").read_text(encoding="utf-8")
    assert [record.event for record in RunJournal(tmp_path / "run.jsonl").records()] == [
        "adaptation_started",
        "adaptation_completed",
    ]
    assert CheckpointStore(tmp_path / "checkpoint.json").load().status == "completed"
