"""Ignored targets: left out of the agent, left alone by install, removed from a workspace with a journal entry."""

import json

from thearc.resources import ContextDocument, MetaAgent, install

CLAUDE = "# Project\n\nUse uv.\n\n# Private notes\n\nMy token lives in 1Password.\n\n## Keys\n\nNone here.\n"


def test_without_ignored_drops_resources_files_and_sections(agent):
    agent = agent.model_copy(update={"context": {"CLAUDE.md": ContextDocument(filename="CLAUDE.md", content=CLAUDE)},
                                     "ignore": ["skill:retry#Retry > Examples", "skill_file:retry/run.sh",
                                                "context:CLAUDE.md#Private notes", "hook:lint", "rule:*"]})
    kept = agent.without_ignored()
    retry = kept.skills["retry"]
    assert "## Examples" not in retry.instructions and "## Limits" in retry.instructions
    assert set(retry.files) == {"notes.md"} and set(kept.hooks) == {"guard"} and not kept.resources
    assert kept.context["CLAUDE.md"].content == "# Project\n\nUse uv.\n\n"
    assert kept.ignore == agent.ignore and agent.skills["retry"].files.keys() == {"notes.md", "run.sh"}


def test_an_installation_is_imported_without_what_is_ignored(agent, tmp_path):
    (tmp_path / "CLAUDE.md").write_text(CLAUDE)
    install(agent, "claude", tmp_path)
    imported = MetaAgent.from_installation("claude", tmp_path, ignore=["skill:retry", "hook:guard"])
    assert not imported.skills and set(imported.hooks) == {"lint"} and imported.ignore == ["hook:guard", "skill:retry"]


def test_install_leaves_ignored_files_and_sections_alone_and_owned_by_nobody(agent, tmp_path):
    agent = agent.model_copy(update={"context": {"CLAUDE.md": ContextDocument(filename="CLAUDE.md", content=CLAUDE)}})
    install(agent, "claude", tmp_path)
    skill, claude = tmp_path / ".claude/skills/retry/SKILL.md", tmp_path / "CLAUDE.md"
    skill.write_text(skill.read_text() + "\nmy own line\n")
    claude.write_text(claude.read_text().replace("None here.", "Still none."))
    ignoring = agent.model_copy(update={"ignore": ["skill:retry", "context:CLAUDE.md#Private notes"]})
    install(ignoring.without_ignored(), "claude", tmp_path)
    assert "my own line" in skill.read_text()  # Neither rewritten nor deleted.
    assert "Still none." in claude.read_text() and "Use uv." in claude.read_text()
    record = json.loads((tmp_path / ".thearc/installed.json").read_text())["agents"]["demo"]
    assert not any("retry" in path for path in record["harnesses"]["claude"]["files"])
    assert record["context"]["CLAUDE.md"] == ["Project"]


def test_ignoring_in_a_workspace_is_a_recorded_change(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    ignored = workspace.ignore_targets(["skill:retry"], reason="Not ours to evolve")
    assert "retry" not in ignored.skills and ignored.ignore == ["skill:retry"]
    removed = [entry for entry in ignored.journal.changes() if entry.reason == "Not ours to evolve"]
    assert {entry.kind for entry in removed} == {"removed"} and removed[0].extra["author"] == "user"
    assert json.loads((tmp_path / "ws/agent.json").read_text())["ignore"] == ["skill:retry"]
    back = ignored.ignore_targets(["skill:retry"], remove=True)
    assert back.ignore == [] and "retry" not in back.skills  # It comes back from the installation.
