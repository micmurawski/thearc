"""Workspace markers: lineage IDs in the files, never in the configuration."""

import json
import re
import subprocess

import pytest

from thearc.resources import Hook, MetaAgent, Skill, record_commits, write_workspace
from thearc.resources.markers import annotate, strip

from .test_reflection import GOOD, SCHEMA, Scripted, answer

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
    targets = workspace.files().targets
    limits = targets[[t.section for t in targets].index("Retry > Limits")]
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
    runtime = Scripted(answer(GOOD))
    Reflector(runtime=runtime).reflect("Review.", MetaAgent.from_workspace(workspace.workspace), SCHEMA)
    assert runtime.texts and not any(MARKER.search(text) or "harmful:" in text for text in runtime.texts.values())


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


def test_a_plain_json_file_in_a_workspace_is_refused(tmp_path):
    root = tmp_path / "ws"
    write_workspace(MetaAgent(name="a", version="1", hooks=[Hook(name="h", command="x")]), root)
    (root / "hooks.jsonc").rename(root / "hooks.json")
    with pytest.raises(ValueError, match="hooks.json: a workspace keeps it as hooks.jsonc"):
        MetaAgent.from_workspace(root)


def test_markers_written_with_ratings_still_read():
    text = "## Limits <!-- res_fd94c5d3172195a16d25e40b · usefulness: (harmful: 3, neutral: 0, helpful: 1) -->\n"
    assert strip("skills/a/SKILL.md", text) == ("## Limits\n", {1: "res_fd94c5d3172195a16d25e40b"})


def test_a_continued_first_line_gets_no_marker():
    agent = MetaAgent(name="a", skills=[Skill(name="s", instructions="# S\n",
                                              files={"run.sh": "#!/bin/sh\nexec x \\\n  --y\n"})])
    files = annotate(agent.files(), {target: target.id for target in agent.files().targets})
    assert files["skills/s/run.sh"] == "#!/bin/sh\nexec x \\\n  --y\n"
