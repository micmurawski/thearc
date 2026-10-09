"""Workspace markers: lineage IDs and ratings in the files, never in the configuration."""

import json
import re
import subprocess

import pytest

from thearc.resources import Hook, MetaAgent, Skill, record_commits
from thearc.resources.markers import strip

from .test_reflection import GOOD, SCHEMA, Scripted, answer, usefulness

MARKER = re.compile(r"res_[0-9a-f]{24}")


def head(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(root, message):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=curator", "-c", "user.email=c@x", "-c",
                    "commit.gpgsign=false", "commit", "-qam", message], check=True)


@pytest.fixture
def workspace(agent, tmp_path):
    agent = agent.model_copy(deep=True)
    agent.skills["retry"].files["run.sh"] = "#!/bin/sh\necho hi\n"
    agent.owns_hooks = ["./guard*"]
    return agent.init_workspace(tmp_path / "ws")


def test_workspace_files_carry_ids_but_the_configuration_does_not(workspace):
    root = workspace.workspace
    limits = workspace.files().targets[[t.section for t in workspace.files().targets].index("Retry > Limits")]
    skill = (root / "skills/retry/SKILL.md").read_text()
    assert f"## Limits <!-- {limits.id} -->" in skill
    assert (root / "skills/retry/run.sh").read_text().startswith("#!/bin/sh\necho hi  # res_")
    hooks = (root / "hooks.jsonc").read_text()
    assert re.search(r'"guard": \{  // res_[0-9a-f]{24}', hooks) and not (root / "hooks.json").exists()
    assert json.loads((root / "owned_hooks.jsonc").read_text()) == ["./guard*"]
    reread = MetaAgent.from_workspace(root)
    assert reread == workspace and reread.revision == workspace.revision
    assert not any(MARKER.search(text) for text in reread.files().files.values())
    assert all(len(text.splitlines()) == len(strip(path, text)[0].splitlines())
               for path, text in reread.files().files.items())


def test_the_reflectors_workspace_has_no_ids_or_ratings(workspace):
    from thearc.reflection import Reflector

    workspace.add_reflection(Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", workspace, SCHEMA))
    workspace.annotate(usefulness)
    assert "harmful: 1" in (workspace.workspace / "skills/retry/SKILL.md").read_text()
    runtime = Scripted(answer(GOOD))
    Reflector(runtime=runtime).reflect("Review.", MetaAgent.from_workspace(workspace.workspace), SCHEMA)
    assert runtime.texts and not any(MARKER.search(text) or "harmful:" in text for text in runtime.texts.values())


def test_annotate_refreshes_ratings_without_touching_the_revision_or_journal(workspace):
    from thearc.reflection import Reflector

    revision, base = workspace.revision, head(workspace.workspace)
    workspace.add_reflection(Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", workspace, SCHEMA))
    sha = workspace.annotate(usefulness)
    assert sha and workspace.annotate(usefulness) is None  # Already current.
    skill = (workspace.workspace / "skills/retry/SKILL.md").read_text()
    assert "## Limits <!-- res_" in skill and "usefulness: (harmful: 1, neutral: 0, helpful: 0) -->" in skill
    reread = MetaAgent.from_workspace(workspace.workspace)
    assert reread.revision == revision
    assert record_commits(workspace.workspace, reread.journal, since=base) == []  # Markers only: nothing to record.


def test_a_kept_marker_makes_a_rename_certain_even_when_the_text_changed(workspace):
    root = workspace.workspace
    lineage = {t.section: i for t, i in workspace.journal.ids().items()}["Retry > Limits"]
    path = root / "skills/retry/SKILL.md"
    text = path.read_text()
    assert "## Limits <!--" in text and "max 3" in text
    path.write_text(text.replace("## Limits <!--", "## Retry budget <!--").replace("max 3", "max 1"))
    base = head(root)
    commit(root, "Rename the limits and lower them")
    journal = MetaAgent.from_workspace(root).journal
    entries = record_commits(root, journal, since=base)
    moved = [e for e in entries if e.kind == "moved"]
    assert [(m.previous.section, m.target.section) for m in moved] == [("Retry > Limits", "Retry > Retry budget")]
    assert {e.kind for e in entries} == {"moved", "modified"} and not [e for e in entries if e.kind == "removed"]
    assert all(e.touched == [lineage] for e in entries) and all(e.reason.startswith("Rename") for e in entries)
    assert {t.section: i for t, i in journal.ids().items()}["Retry > Retry budget"] == lineage


def test_a_heading_without_a_marker_is_new_and_a_copied_marker_is_an_error(workspace):
    root = workspace.workspace
    path = root / "skills/retry/SKILL.md"
    path.write_text(path.read_text() + "\n## Backoff\n\nWait between attempts.\n")
    base = head(root)
    commit(root, "Add backoff")
    journal = MetaAgent.from_workspace(root).journal
    (added,) = record_commits(root, journal, since=base)
    assert added.kind == "added" and added.target.section == "Retry > Backoff"
    assert added.touched == [added.target.id]
    marker = re.search(r"## Limits (<!-- res_[0-9a-f]{24} -->)", path.read_text())[1]
    path.write_text(path.read_text().replace("## Backoff", f"## Backoff {marker}"))
    with pytest.raises(ValueError, match="appears on both"):
        MetaAgent.from_workspace(root)


def test_a_commit_touching_two_sections_lists_both(workspace):
    root = workspace.workspace
    path = root / "skills/retry/SKILL.md"
    path.write_text(path.read_text().replace("ex1", "ex2").replace("max 3", "max 2"))
    base = head(root)
    commit(root, "Tighten examples and limits")
    entries = record_commits(root, MetaAgent.from_workspace(root).journal, since=base)
    assert len(entries) == 2 and entries[0].touched == entries[1].touched and len(entries[0].touched) == 2


def test_workspaces_in_the_earlier_layout_still_load(tmp_path):
    root = tmp_path / "old"
    agent = MetaAgent(name="a", version="1", skills=[Skill(name="s", instructions="# S\n")],
                      hooks=[Hook(name="h", command="graphify x")], owns_hooks=["graphify *"])
    agent.to_workspace(root)
    (root / "hooks.jsonc").rename(root / "hooks.json")
    (root / "owned_hooks.jsonc").unlink()
    manifest = json.loads((root / "agent.json").read_text())
    (root / "agent.json").write_text(json.dumps({**manifest, "owns_hooks": ["graphify *"]}))
    assert MetaAgent.from_workspace(root) == agent
