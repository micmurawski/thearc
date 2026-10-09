"""Sections and their subsections: containment, rating roll-up, and change history of a subtree."""

from thearcv2.resources import (
    CategoricalScale,
    MetaAgent,
    ReflectionSchema,
    ResourceTarget,
    count_all,
    detect_changes,
    including_subsections,
    since_last_modification,
)

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)
SKILL = ResourceTarget(kind="skill", name="retry")
RETRY = SKILL.model_copy(update={"section": "Retry"})
EXAMPLES = SKILL.model_copy(update={"section": "Retry > Examples"})
LIMITS = SKILL.model_copy(update={"section": "Retry > Limits"})
REF = "skills/retry/SKILL.md"


def rate(agent: MetaAgent, *items: tuple[str, str]) -> None:
    result = ReflectionSchema(usefulness).record(
        [{"ref": f"{REF}#{lines}", "rating": value} for lines, value in items], agent
    )
    agent.journal.record(*result.entries)


def counts(agent: MetaAgent, target: ResourceTarget, policy=count_all) -> dict[str, float] | None:
    summary = agent.journal.summaries(usefulness, policy).get(target)
    return dict(summary.counts) if summary else None


def test_containment_follows_heading_paths_within_one_resource():
    assert SKILL.contains(RETRY) and RETRY.contains(EXAMPLES) and RETRY.contains(RETRY)
    assert not EXAMPLES.contains(RETRY) and not EXAMPLES.contains(LIMITS)
    assert not RETRY.contains(ResourceTarget(kind="skill", name="retry", section="Retry Later"))
    assert not SKILL.contains(ResourceTarget(kind="skill_file", name="retry/notes.md"))
    assert EXAMPLES.ancestors() == [RETRY, SKILL] and SKILL.ancestors() == []


def test_ratings_roll_up_to_parents_but_not_down(agent):
    rate(
        agent, ("L9", "neutral"), ("L13", "harmful"), ("L17", "helpful"), ("L13-L17", "harmful")
    )  # One judgment covering both subsections.
    assert counts(agent, RETRY) == {"harmful": 0, "neutral": 1, "helpful": 0}
    subtree = including_subsections()
    assert counts(agent, RETRY, subtree) == {"harmful": 2, "neutral": 1, "helpful": 1}
    assert counts(agent, SKILL, subtree) == counts(agent, RETRY, subtree)
    assert counts(agent, EXAMPLES, subtree) == {"harmful": 2, "neutral": 0, "helpful": 0}
    assert counts(agent, LIMITS, subtree) == {"harmful": 1, "neutral": 0, "helpful": 1}


def test_roll_up_composes_with_other_policies(agent):
    rate(agent, ("L13", "harmful"), ("L17", "helpful"), ("L13-L17", "harmful"))
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("max 3", "max 5")
    edited.journal.record(*detect_changes(agent, edited))
    # Limits changed, so its ratings no longer count; the spanning judgment still counts through Examples.
    recent = including_subsections(since_last_modification)
    assert counts(edited, RETRY, recent) == {"harmful": 2, "neutral": 0, "helpful": 0}


def test_changes_and_diff_of_a_subtree(agent):
    agent.journal.record(*detect_changes(None, agent, reason="import"))
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace(
        "max 3\n", "max 5\n\n## Backoff\n\nDouble the wait.\n"
    )
    edited.context["AGENTS.md"].content = "# Rules\n\nBe precise.\n"
    edited.journal.record(*detect_changes(agent, edited, reason="tune retries", commit="c1"))

    under_retry = edited.journal.changes(RETRY, subsections=True)
    assert {(change.kind, change.target.section) for change in under_retry if change.reason == "tune retries"} == {
        ("modified", "Retry > Limits"),
        ("added", "Retry > Backoff"),
    }
    assert {change.target.section for change in under_retry} == {
        "Retry",
        "Retry > Examples",
        "Retry > Limits",
        "Retry > Backoff",
    }
    assert [c.target for c in edited.journal.changes(RETRY)] == [RETRY]  # Without subsections: the section only.

    diff = edited.journal.diff(under=RETRY)
    assert "Retry > Limits" in diff and "Retry > Backoff" in diff and "AGENTS.md" not in diff


def test_cached_lineages_follow_every_change_to_the_entries(agent):
    journal = agent.journal
    assert journal.lineages() == []
    journal.record(*detect_changes(None, agent))
    grouped = journal.lineages()
    assert {lineage.target for lineage in grouped} == set(agent.files().targets)
    assert journal.lineages() == grouped  # Served from the cache.
    rate(agent, ("L13", "harmful"))
    assert len(journal.history(EXAMPLES)) == 2  # record() invalidates.
    journal.entries = journal.entries[:1]
    assert len(journal.lineages()) == 1  # Replacing the list invalidates.
    journal.entries.append(agent.journal.entries[0].model_copy(update={"id": "z"}))
    assert sum(len(lineage.entries) for lineage in journal.lineages()) == 2  # So does appending in place.
    copy = agent.model_copy(deep=True)
    assert copy.journal == agent.journal  # The cache is not part of the model, so equality is unaffected.
