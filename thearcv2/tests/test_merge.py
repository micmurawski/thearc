"""Worktree candidates and merging, without model calls: scripted curators and a scripted merger."""

import subprocess
from types import SimpleNamespace

import pytest

from thearcv2.curation import Curator, Merger
from thearcv2.reflection import Reflector
from thearcv2.resources import MetaAgent

from .test_curation import ScriptedCurator, edit
from .test_reflection import GOOD, SCHEMA, Scripted, answer


def head(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def pipeline(candidate, *edits_and_reasons):
    """Reflect (Limits is harmful), then curate: each (old, new, reason) one commit."""
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", candidate, SCHEMA)
    candidate.add_reflection(reflection)

    def script(root, tool):
        for old, new, reason in edits_and_reasons:
            edit(root, old, new)
            assert not tool("commit_change", message=reason)["error"]
        tool("finish", summary="Done.")

    return Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [reflection], candidate)


@pytest.fixture
def candidates(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    one, two = workspace.to_worktree(tmp_path / "c1"), workspace.to_worktree(tmp_path / "c2")
    fork_point = head(workspace.workspace)
    first = pipeline(one, ("max 3", "max 1", "One retry is enough"))
    second = pipeline(two, ("max 3", "max 2, never on syntax errors", "Never retry syntax errors"),
                      ("ex1", "ex2", "Show a transient failure"))
    return SimpleNamespace(workspace=workspace, one=one, two=two, first=first, second=second, fork_point=fork_point)


def test_a_worktree_is_its_pipelines_outcome(candidates):
    workspace, one, first, fork_point = candidates.workspace, candidates.one, candidates.first, candidates.fork_point
    assert one.workspace.name == "c1" and head(one.workspace) == first.head  # Moved forward by curation…
    assert head(workspace.workspace) == fork_point  # …while the workspace stays where they forked.
    assert "max 1" in MetaAgent.from_workspace(one.workspace).skills["retry"].instructions
    assert "max 3" in MetaAgent.from_workspace(workspace.workspace).skills["retry"].instructions


def test_the_merger_decides_every_changed_section_from_the_proposals(candidates, tmp_path):
    one, two, fork_point = candidates.one, candidates.two, candidates.fork_point

    def script(root, tool):
        edit(root, "max 3", "max 2; never retry syntax errors")
        assert not tool("commit_change", message="Combine candidate/c1 and candidate/c2 limits")["error"]
        edit(root, "ex1", "ex2")
        assert not tool("commit_change", message="Take candidate/c2's example")["error"]
        tool("finish", summary="Combined the limits; took the example.")

    runtime = ScriptedCurator(script)
    merge = Merger(runtime=runtime).merge("Merge them.", [one, two])
    assert merge.base == fork_point and set(merge.candidates) == {"candidate/c1", "candidate/c2"}
    by_section = {p.target.section: p for p in merge.proposals}
    assert set(by_section) == {"Retry > Limits", "Retry > Examples"}
    limits = by_section["Retry > Limits"]
    assert limits.text == "max 3" and {(v.candidate, v.text) for v in limits.versions} == {
        ("candidate/c1", "max 1"), ("candidate/c2", "max 2, never on syntax errors")}
    assert all(len(v.ratings) == 1 for v in limits.versions)  # Each pipeline's own rating of the section.
    prompt = runtime.sent[0]
    assert "One retry is enough" in prompt and "Never retry syntax errors" in prompt and "**At the base:**" in prompt

    merged = merge.agent
    assert merged.workspace == (tmp_path / merge.branch.replace("/", "-")).resolve()
    assert "max 2; never retry syntax errors" in merged.skills["retry"].instructions and "ex2" in (
        merged.skills["retry"].instructions)
    changes = [e for e in merge.entries if e.kind != "rated"]
    assert {e.reason for e in changes} == {"Combine candidate/c1 and candidate/c2 limits",
                                          "Take candidate/c2's example"}
    assert all(e.extra["merge"] == merge.id for e in changes)
    ratings = merged.journal.ratings()
    assert len(ratings) == 2 and len({r.extra["reflection"] for r in ratings}) == 2  # Both pipelines' ratings.
    assert not [e for e in merged.journal.changes() if "curation" in e.extra]  # Curators' changes stay behind.


def test_candidates_must_share_one_base(candidates, tmp_path):
    one, two = candidates.one, candidates.two
    later = MetaAgent.from_workspace(one.workspace).to_worktree(tmp_path / "c3")
    with pytest.raises(ValueError, match="share one base"):  # c3 started from c1's result, not from the base.
        Merger(runtime=ScriptedCurator(lambda root, tool: None)).merge("Merge.", [one, later, two])
    with pytest.raises(ValueError, match="at least two"):
        Merger(runtime=ScriptedCurator(lambda root, tool: None)).merge("Merge.", [one])
