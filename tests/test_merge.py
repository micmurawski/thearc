"""Worktree candidates and merging, without model calls: scripted curators and a scripted merger."""

import subprocess

import pytest

from thearc.curation import Merger
from thearc.resources import MetaAgent

from .pipelines import head, pipeline
from .test_curation import ScriptedCurator, edit


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

    released = merged.release()  # Versioning, as the cookbook's run ends: the merged candidate becomes 1.0.1.
    assert released.version == "1.0.1" and released.workspace == merged.workspace
    assert subprocess.run(["git", "-C", str(merged.workspace), "rev-parse", "--verify", "release/1.0.1"],
                          capture_output=True, check=False).returncode == 0


def test_candidates_must_share_one_base(candidates, tmp_path):
    one, two = candidates.one, candidates.two
    later = MetaAgent.from_workspace(one.workspace).to_worktree(tmp_path / "c3")
    with pytest.raises(ValueError, match="share one base"):  # c3 started from c1's result, not from the base.
        Merger(runtime=ScriptedCurator(lambda root, tool: None)).merge("Merge.", [one, later, two])
    with pytest.raises(ValueError, match="at least two"):
        Merger(runtime=ScriptedCurator(lambda root, tool: None)).merge("Merge.", [one])


def test_the_evolution_graph_shows_candidates_merging_into_a_release(candidates, tmp_path):
    from thearc.resources.evolution import evolution

    one, two = candidates.one, candidates.two

    def script(root, tool):
        edit(root, "max 3", "max 2; never retry syntax errors")
        assert not tool("commit_change", message="Combine the limits")["error"]
        tool("finish", summary="Combined.")

    merge = Merger(runtime=ScriptedCurator(script)).merge("Merge them.", [one, two])
    merge.agent.release()
    dead = MetaAgent.from_workspace(candidates.workspace.workspace).to_worktree(tmp_path / "c3")  # Never merged.
    assert dead.workspace is not None

    graph = evolution(candidates.workspace.workspace)["graph"]
    (run,) = [r for r in graph["runs"] if r["merges"]]
    assert [c["branch"] for c in run["candidates"]] == ["candidate/c1", "candidate/c2"]
    assert [len(c["commits"]) for c in run["candidates"]] == [1, 2]
    (merged,) = run["merges"]
    assert merged["candidates"] == ["candidate/c1", "candidate/c2"]  # Recorded on the merge's entries.
    assert merged["release"]["version"] == "1.0.1" and graph["releases"][0]["merge"] == merged["branch"]
    texts = next(iter(t for t in run["sections"].values() if t["base"] and "max 3" in t["base"]))
    assert "max 1" in texts["heads"]["candidate/c1"] and "max 2; never" in texts["heads"][merged["branch"]]
    (stray,) = [r for r in graph["runs"] if r["id"] == "candidate/c3"]
    assert stray["candidates"][0]["merged_into"] == []  # A dead branch.


def test_a_run_is_stale_only_when_its_base_misses_an_earlier_release(candidates, tmp_path):
    from thearc.resources.evolution import evolution
    from thearc.resources.graph import mentioned

    root = candidates.workspace.workspace

    def merger(root, tool):
        edit(root, "max 3", "max 2")
        assert not tool("commit_change", message="Combine the limits")["error"]
        tool("finish", summary="Combined.")

    Merger(runtime=ScriptedCurator(merger)).merge("Merge.", [candidates.one, candidates.two]).agent.release()
    late = MetaAgent.from_workspace(root).to_worktree(tmp_path / "late")  # main never moved: still the old base
    pipeline(late, ("ex1", "ex3", "Another example"))
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--ff-only", "release/1.0.1"], check=True)
    fresh = MetaAgent.from_workspace(root).to_worktree(tmp_path / "fresh")  # forked from the release
    pipeline(fresh, ("ex1", "ex4", "Yet another example"))

    runs = {r["id"]: r for r in evolution(root)["graph"]["runs"]}
    assert runs["candidate/late"]["stale_base"] == "1.0.1"
    assert runs["candidate/fresh"]["stale_base"] is None and runs["candidate/fresh"]["base_version"] == "1.0.1"
    (merged,) = [r for r in runs.values() if r["merges"]]
    assert merged["merges"][0]["release"]["version"] == "1.0.1"
    assert mentioned(["candidate/c1", "candidate/c10"], "took candidate/c10") == ["candidate/c10"]


def test_a_released_merge_keeps_its_candidates_once_the_trunk_moves_past_it(candidates, tmp_path):
    from thearc.resources.evolution import evolution

    root = candidates.workspace.workspace

    def merger(root, tool):
        edit(root, "max 3", "max 2")
        assert not tool("commit_change", message="Combine the limits")["error"]
        tool("finish", summary="Combined.")

    Merger(runtime=ScriptedCurator(merger)).merge("Merge.", [candidates.one, candidates.two]).agent.release()
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--ff-only", "release/1.0.1"], check=True)
    idle = MetaAgent.from_workspace(root).to_worktree(tmp_path / "idle")  # The next run; no commits yet.
    assert idle.workspace is not None

    graph = evolution(root)["graph"]
    (run,) = [r for r in graph["runs"] if r["merges"]]
    assert run["merges"][0]["candidates"] == ["candidate/c1", "candidate/c2"] and run["merges"][0]["commits"]
    assert [c["merged_into"] for c in run["candidates"]] == [[run["merges"][0]["branch"]]] * 2
    assert graph["releases"][0]["parent_version"] == run["base_version"] != "1.0.1"
    (following,) = [r for r in graph["runs"] if r["id"] == "candidate/idle"]
    assert following["base_version"] == "1.0.1" and following["candidates"][0]["commits"] == []


def test_a_workspace_inside_a_project_repository_keeps_to_its_prefixed_branches(agent, tmp_path):
    from thearc.resources.evolution import evolution

    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "Project")
    workspace = agent.init_workspace(project / ".thearc/workspace", repository=project, prefix="thearc/")
    root = workspace.workspace
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "thearc/main"
    assert git(root, "rev-list", "--count", "HEAD") == "1"  # An orphan history: no project commits in it.
    one, two = workspace.to_worktree(tmp_path / "c1"), workspace.to_worktree(tmp_path / "c2")
    pipeline(one, ("max 3", "max 1", "One retry is enough"))
    pipeline(two, ("ex1", "ex2", "Show a transient failure"))

    def script(root, tool):
        edit(root, "max 3", "max 1")
        assert not tool("commit_change", message="Take thearc/candidate/c1's limit")["error"]
        tool("finish", summary="Took the limit.")

    merge = Merger(runtime=ScriptedCurator(script)).merge("Merge them.", [one, two])
    released = merge.agent.release()
    assert merge.branch.startswith("thearc/merge/") and released.version == "1.0.1"
    branches = git(project, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    assert branches[0] == "main" and all(b.startswith("thearc/") for b in branches[1:])
    assert {"thearc/candidate/c1", "thearc/release/1.0.1"} <= set(branches)

    graph = evolution(root)["graph"]
    assert graph["trunk"] == "thearc/main"
    (run,) = [r for r in graph["runs"] if r["merges"]]
    assert [c["branch"] for c in run["candidates"]] == ["thearc/candidate/c1", "thearc/candidate/c2"]
    assert run["merges"][0]["candidates"] == ["thearc/candidate/c1", "thearc/candidate/c2"]
    assert graph["releases"][0]["merge"] == merge.branch and graph["releases"][0]["version"] == "1.0.1"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()
