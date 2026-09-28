import pytest

from thearc import MCP, ContextDocument, Hook, MetaAgent, ResourceTarget, Skill
from thearc.learning.ace.pipeline import Reflection, ReflectionItem, ReflectionRating
from thearc.learning.curation import AssessmentLedger, propagate_ranks
from thearc.learning.evidence.snapshot import content_hash
from thearc.models import Ranks


def config():
    return MetaAgent(name="agent", skills=[Skill(name="graphify", ranks=Ranks(helpful=3))])


def reflection(agent, *, identity="r1", event="e1", rating="helpful", target=None):
    return Reflection(
        id=identity, session_ids=["s1"],
        items=[ReflectionItem(
            target=target or ResourceTarget(kind="skill", name="graphify"),
            rating=rating, reason="Observed useful configuration", limitations=["Only one task"],
            evidence=[{"session_id": "s1", "event_id": event}],
        )],
        raw={"manifest": {"config_sha256": content_hash(agent.without_ranks().model_dump(mode="json"))}},
    )


def test_production_items_propagate_once_and_preserve_provenance():
    original = config()
    observation = reflection(original)
    result, ledger = propagate_ranks(original, [observation], epoch_id="epoch-1")
    assert original.skills["graphify"].ranks.helpful == 3
    assert result.skills["graphify"].ranks.helpful == 4
    assert ledger.baselines[0].ranks.helpful == 3
    item = ledger.assessments[0]
    assert item.epoch_id == "epoch-1"
    assert item.limitations == ["Only one task"]
    assert "unverified" in item.configuration_provenance
    restored = AssessmentLedger.model_validate_json(ledger.model_dump_json())
    repeated, history = propagate_ranks(result, [observation], ledger=restored, epoch_id="epoch-2")
    assert repeated == result
    assert history == restored
    observation.items[0].evidence[0].event_id = "mutated"
    assert ledger.assessments[0].evidence[0].event_id == "e1"


def test_overlap_kept_but_not_counted_and_new_evidence_counts():
    original = config()
    target = ResourceTarget(kind="skill", name="graphify")
    result, history = propagate_ranks(original, [
        reflection(original), reflection(original, identity="r2", rating="harmful"),
        reflection(original, identity="r3", event="e2", rating="neutral"),
    ], epoch_id="epoch")
    assert result.rank_for(target) == Ranks(helpful=4, neutral=1)
    assert history.assessments[1].rating == "harmful"
    assert not history.assessments[1].counted
    assert history.assessments[1].overlaps == [history.assessments[0].id]


def test_edit_keeps_old_history_without_attributing_it_to_new_content():
    original = config()
    result, history = propagate_ranks(original, [reflection(original)], epoch_id="first")
    result.skills["graphify"].instructions = "New instructions"
    result, history = propagate_ranks(result, [reflection(result, identity="new", event="new")],
                                      ledger=history, epoch_id="second")
    target = ResourceTarget(kind="skill", name="graphify")
    assert result.rank_for(target) == Ranks(helpful=1)
    assert history.ranks(target) == Ranks(helpful=5)
    assert len({item.revision for item in history.assessments}) == 2
    result.skills.clear()
    empty, retained = propagate_ranks(result, [], ledger=history, epoch_id="third")
    assert not empty.skills
    assert retained == history


def test_parent_and_section_counts_do_not_overwrite_each_other():
    original = MetaAgent(
        name="agent", context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nUse graphify")],
    )
    parent = ResourceTarget(kind="context", name="AGENTS.md")
    section = ResourceTarget(kind="context", name="AGENTS.md", section="Rules")
    result, history = propagate_ranks(original, [
        reflection(original, target=parent),
        reflection(original, target=section, identity="section", rating="harmful"),
        reflection(original, target=section.model_copy(update={"section": "rules"}), identity="alternate"),
    ], epoch_id="epoch")
    assert result.rank_for(parent) == Ranks(helpful=1)
    assert result.rank_for(section) == Ranks(harmful=1)
    repeated, _ = propagate_ranks(result, [], ledger=history, epoch_id="epoch2")
    assert repeated == result


@pytest.mark.parametrize("problem", ["stale", "missing", "evidence", "target", "reason", "epoch"])
def test_invalid_batches_do_not_mutate_inputs(problem):
    original = config()
    good = reflection(original)
    bad = reflection(original, identity="bad")
    epoch = "epoch"
    if problem == "stale":
        bad.raw["manifest"]["config_sha256"] = "stale"
    elif problem == "missing":
        bad.raw = {}
    elif problem == "evidence":
        bad.items[0].evidence[0].session_id = "another"
    elif problem == "target":
        bad.items[0].target = ResourceTarget(kind="skill", name="missing")
    elif problem == "reason":
        bad.items[0].reason = " "
    else:
        epoch = " "
    ledger = AssessmentLedger()
    with pytest.raises((ValueError, KeyError)):
        propagate_ranks(original, [good, bad], epoch_id=epoch, ledger=ledger)
    assert ledger == AssessmentLedger()
    assert original == config()


def test_legacy_input_requires_explicit_opt_in_and_identity_is_immutable():
    original = config()
    item = reflection(original)
    item.raw = {}
    result, ledger = propagate_ranks(original, [item], epoch_id="epoch", allow_unverified=True)
    item.items[0].rating = ReflectionRating.HARMFUL
    with pytest.raises(ValueError, match="identity reused"):
        propagate_ranks(result, [item], ledger=ledger, epoch_id="epoch", allow_unverified=True)


def test_hook_and_skill_file_ranks():
    original = MetaAgent(
        name="agent",
        hooks=[Hook(name="guard")],
        skills=[Skill(name="graphify", files={"references/query.md": "# Query\n\nUse it"})],
    )
    targets = [ResourceTarget(kind="hook", name="guard"),
               ResourceTarget(kind="skill_file", name="graphify/references/query.md"),
               ResourceTarget(kind="skill_file", name="graphify/references/query.md", section="Query")]
    result, _ = propagate_ranks(original, [reflection(original, identity=str(i), target=target)
                                          for i, target in enumerate(targets)], epoch_id="epoch")
    assert all(result.rank_for(target) == Ranks(helpful=1) for target in targets)


def test_executable_and_mcp_assessments_never_rewrite_configuration():
    original = MetaAgent(
        name="agent",
        skills=[Skill(name="graphify", files={"scripts/query.py": "print('hello')\n"})],
        mcps=[MCP(name="server", command="never-execute")],
    )
    targets = [ResourceTarget(kind="skill_file", name="graphify/scripts/query.py"),
               ResourceTarget(kind="mcp", name="server")]
    result, history = propagate_ranks(original, [
        reflection(original, identity=str(i), target=target) for i, target in enumerate(targets)
    ], epoch_id="epoch")
    assert result == original
    assert all(history.ranks(target) == Ranks(helpful=1) for target in targets)


def test_reference_edit_does_not_reset_parent_skill_counts():
    original = config()
    original.skills["graphify"].files["reference.md"] = "Before"
    result, history = propagate_ranks(original, [reflection(original)], epoch_id="first")
    result.skills["graphify"].files["reference.md"] = "After"
    updated, _ = propagate_ranks(result, [], ledger=history, epoch_id="second")
    assert updated.skills["graphify"].ranks.helpful == 4


def test_pre_version_reflection_hash_remains_usable_for_unversioned_agents():
    original = config()
    observed = reflection(original)
    legacy = original.without_ranks().model_dump(mode="json")
    legacy.pop("version")
    observed.raw["manifest"]["config_sha256"] = content_hash(legacy)
    result, _ = propagate_ranks(original, [observed], epoch_id="legacy")
    assert result.skills["graphify"].ranks.helpful == 4
    original.version = "1.0.0"
    with pytest.raises(ValueError, match="different configuration"):
        propagate_ranks(original, [observed], epoch_id="versioned")
