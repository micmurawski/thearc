"""Drift: hand edits to an installation are committed to the workspace before anything overwrites them."""

from thearc.resources import Hook, MetaAgent


def installed(agent, tmp_path, *harnesses):
    workspace = agent.init_workspace(tmp_path / "ws")
    project = tmp_path / "project"
    for harness in harnesses:
        workspace.install(harness, project)
    return workspace, project


def test_a_hand_edit_is_committed_as_the_users_change_and_kept_by_the_next_install(agent, tmp_path):
    workspace, project = installed(agent, tmp_path, "claude", "codex")
    assert not workspace.sync_installation(project, ["claude", "codex"]).changed  # Nothing drifted.
    skill = project / ".claude/skills/retry/SKILL.md"
    skill.write_text(skill.read_text().replace("max 3", "max 4"))
    drift = workspace.sync_installation(project, ["claude", "codex"])
    (entry,) = [e for e in drift.entries if e.kind == "modified"]
    limits = {t.section: i for t, i in workspace.journal.ids().items()}["Retry > Limits"]
    assert entry.target.section == "Retry > Limits" and entry.extra["author"] == "user" and not drift.conflicts
    assert {t.section: i for t, i in drift.agent.journal.ids().items()}["Retry > Limits"] == limits  # One history.
    drift.agent.install("codex", project)
    assert "max 4" in (project / ".codex/skills/retry/SKILL.md").read_text()


def test_copies_edited_differently_conflict_and_a_removal_needs_every_copy(agent, tmp_path):
    workspace, project = installed(agent, tmp_path, "claude", "codex")
    for harness, limit in (("claude", "max 4"), ("codex", "max 5")):
        skill = project / f".{harness}/skills/retry/SKILL.md"
        skill.write_text(skill.read_text().replace("max 3", limit))
    (project / ".claude/rules/style.md").unlink()  # Still in Codex's copy…
    drift = workspace.sync_installation(project, ["claude", "codex"])
    assert drift.conflicts == ("skills/retry",) and "max 4" in drift.agent.skills["retry"].instructions
    assert "rules/style.md" in drift.agent.resources  # …so it is kept.


def test_what_a_harness_cannot_install_is_not_a_removal(agent, tmp_path):
    scoped = Hook(name="guard", command="./guard.sh", harnesses=["claude"])
    agent = agent.model_copy(update={"hooks": {"guard": scoped}})
    workspace, project = installed(agent, tmp_path, "claude", "codex", "pi")
    drift = workspace.sync_installation(project, ["claude", "codex", "pi"])
    assert not drift.changed and set(drift.agent.hooks) == {"guard"} and set(drift.agent.mcps) == {"docs"}
    assert MetaAgent.from_workspace(workspace.workspace).revision == workspace.revision
