import os
import subprocess
from pathlib import Path

import pytest

from thearcv2.resources import Journal, ResourceTarget, agent_at, record_commits, write_workspace

SKILL = Path("skills/retry/SKILL.md")
EXAMPLES = ResourceTarget(kind="skill", name="retry", section="Retry > Examples")
LIMITS = ResourceTarget(kind="skill", name="retry", section="Retry > Limits")
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Curator",
    "GIT_AUTHOR_EMAIL": "c@example.test",
    "GIT_COMMITTER_NAME": "Curator",
    "GIT_COMMITTER_EMAIL": "c@example.test",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


@pytest.fixture
def repo(agent, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write_workspace(agent, root, journal=False, overwrite=True)
    commit(root, "Initial configuration")
    return root


def kinds(entries):
    return [(entry.kind, entry.target) for entry in entries]


def test_first_commit_adds_everything_with_its_message(agent, repo):
    journal = Journal()
    entries = record_commits(repo, journal)
    assert {entry.target for entry in entries} == set(agent.files().targets)
    assert {(entry.kind, entry.reason) for entry in entries} == {("added", "Initial configuration")}
    assert entries[0].extra == {"author": "Curator"}


def test_one_commit_per_section_edit(repo):
    journal = Journal()
    record_commits(repo, journal)
    edit(repo / SKILL, "max 3", "max 2")
    sha = commit(repo, "Lower the retry limit: 3 retries looped on permanent errors")
    entries = record_commits(repo, journal)
    assert kinds(entries) == [("modified", LIMITS)]
    change = entries[0]
    assert (change.commit, change.before, change.after) == (sha, "max 3", "max 2")
    assert change.reason == "Lower the retry limit: 3 retries looped on permanent errors"


def test_a_commit_touching_two_sections_records_two_entries(repo):
    journal = Journal()
    record_commits(repo, journal)
    edit(repo / SKILL, "ex1", "retry(fetch)")
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Make retry guidance concrete")
    entries = record_commits(repo, journal)
    assert kinds(entries) == [("modified", EXAMPLES), ("modified", LIMITS)]
    assert {entry.reason for entry in entries} == {"Make retry guidance concrete"}


def test_new_section_is_added_and_gets_an_id(repo):
    journal = Journal()
    record_commits(repo, journal)
    edit(repo / SKILL, "max 3\n", "max 3\n\n## Backoff\n\nDouble the wait each time.\n")
    commit(repo, "Add backoff guidance")
    backoff = ResourceTarget(kind="skill", name="retry", section="Retry > Backoff")
    entries = record_commits(repo, journal)
    assert kinds(entries) == [("added", backoff)]
    assert entries[0].content == "Double the wait each time."
    assert backoff.id.startswith("res_")
    assert [change.reason for change in journal.changes(backoff)] == ["Add backoff guidance"]


def test_rename_keeps_the_section_history(repo):
    journal = Journal()
    record_commits(repo, journal)
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Lower the limit")
    edit(repo / SKILL, "## Limits", "## Bounds")
    commit(repo, "Rename Limits to Bounds")
    record_commits(repo, journal)
    bounds = LIMITS.model_copy(update={"section": "Retry > Bounds"})
    assert [(change.kind, change.reason) for change in journal.changes(bounds)] == [
        ("added", "Initial configuration"),
        ("modified", "Lower the limit"),
        ("moved", "Rename Limits to Bounds"),
    ]


def test_running_again_records_nothing_and_unrelated_commits_are_skipped(repo):
    journal = Journal()
    record_commits(repo, journal)
    (repo / ".notes").write_text("not configuration\n")
    commit(repo, "Notes")
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Lower the limit")
    assert len(record_commits(repo, journal)) == 1
    assert record_commits(repo, journal) == []


def test_committed_journal_is_ignored(agent, repo):
    journal = Journal()
    record_commits(repo, journal)
    agent.journal = journal
    write_workspace(agent, repo, overwrite=True)
    commit(repo, "Record the journal")
    assert record_commits(repo, journal) == []
    assert agent_at(repo, "HEAD").revision == agent.revision


def test_workspace_in_a_subdirectory(agent, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("project\n")
    commit(root, "Project")
    write_workspace(agent, root / "agents" / "demo", journal=False)
    commit(root, "Add agent")
    edit(root / "agents/demo" / SKILL, "max 3", "max 2")
    commit(root, "Lower the limit")
    journal = Journal()
    entries = record_commits(root, journal, path="agents/demo")
    assert {entry.reason for entry in entries} == {"Add agent", "Lower the limit"}
    assert kinds(entries)[-1] == ("modified", LIMITS)


def test_since_adopts_an_existing_journal(agent, human, repo):
    from thearcv2.resources import CategoricalScale, ReflectionSchema, detect_changes

    usefulness = CategoricalScale("u", ["bad", "good"])
    agent.journal.record(*detect_changes(None, agent, **human))
    agent.journal.record(
        *ReflectionSchema(usefulness).record([{"ref": f"{SKILL}#L17", "rating": "good"}], agent).entries
    )
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Lower the limit")
    entries = record_commits(repo, agent.journal, since="HEAD~1")
    assert kinds(entries) == [("modified", LIMITS)]
    assert agent.journal.summaries(usefulness)[LIMITS].counts == {"bad": 0, "good": 1}


def test_adding_a_live_target_again_keeps_its_history(agent, human):
    from thearcv2.resources import CategoricalScale, ReflectionSchema, detect_changes

    usefulness = CategoricalScale("u", ["bad", "good"])
    agent.journal.record(*detect_changes(None, agent, **human))
    agent.journal.record(
        *ReflectionSchema(usefulness).record([{"ref": f"{SKILL}#L17", "rating": "good"}], agent).entries
    )
    agent.journal.record(*detect_changes(None, agent, **human))
    assert agent.journal.summaries(usefulness)[LIMITS].counts == {"bad": 0, "good": 1}


def test_journal_diff_between_commits(repo):
    journal = Journal()
    record_commits(repo, journal)
    start = git(repo, "rev-parse", "HEAD")
    edit(repo / SKILL, "max 3", "max 2")
    middle = commit(repo, "Lower the limit")
    edit(repo / SKILL, "## Limits", "## Bounds")
    commit(repo, "Rename Limits to Bounds")
    edit(repo / SKILL, "ex1", "retry(fetch)")
    commit(repo, "Concrete example")
    record_commits(repo, journal)

    everything = journal.diff(start[:7])
    assert "=== skill:retry#Retry > Bounds  (was skill:retry#Retry > Limits)" in everything
    assert "Lower the limit" in everything and "Rename Limits to Bounds" in everything
    assert "-max 3\n+max 2" in everything
    assert "=== skill:retry#Retry > Examples" in everything and "+retry(fetch)" in everything

    only_middle = journal.diff(start, head=middle)
    assert "Lower the limit" in only_middle and "Bounds" not in only_middle and "Examples" not in only_middle
    assert journal.diff(git(repo, "rev-parse", "HEAD")) == ""
    with pytest.raises(ValueError, match="exactly one commit"):
        journal.diff("0000000")


def test_modified_entry_diff(repo):
    journal = Journal()
    record_commits(repo, journal)
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Lower the limit")
    (change,) = record_commits(repo, journal)
    assert change.diff().splitlines() == [
        "--- a/skill:retry#Retry > Limits",
        "+++ b/skill:retry#Retry > Limits",
        "@@ -1 +1 @@",
        "-max 3",
        "+max 2",
    ]


def test_journal_diff_from_a_commit_that_recorded_nothing(agent, repo):
    base = git(repo, "rev-parse", "HEAD")
    edit(repo / SKILL, "max 3", "max 2")
    commit(repo, "Lower the limit")
    record_commits(repo, agent.journal, since=base)
    assert journal_diff_lines(agent.journal.diff(base[:8])) == [
        "=== skill:retry#Retry > Limits",
        "    {sha} Lower the limit",
        "--- a/skill:retry#Retry > Limits",
        "+++ b/skill:retry#Retry > Limits",
        "@@ -1 +1 @@",
        "-max 3",
        "+max 2",
    ]


def journal_diff_lines(text):
    lines = text.splitlines()
    lines[1] = "    {sha}" + lines[1][12:]
    return lines
