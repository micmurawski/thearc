"""Scripted pipelines for tests: reflect, curate, and parallel candidates on worktrees, without model calls."""

import subprocess
from types import SimpleNamespace

from thearc.curation import Curator
from thearc.reflection import Reflector

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


def make_candidates(agent, tmp_path):
    """A workspace and two worktree candidates curated from it: c1 one commit, c2 two."""
    workspace = agent.init_workspace(tmp_path / "ws")
    one, two = workspace.to_worktree(tmp_path / "c1"), workspace.to_worktree(tmp_path / "c2")
    fork_point = head(workspace.workspace)
    first = pipeline(one, ("max 3", "max 1", "One retry is enough"))
    second = pipeline(two, ("max 3", "max 2, never on syntax errors", "Never retry syntax errors"),
                      ("ex1", "ex2", "Show a transient failure"))
    return SimpleNamespace(workspace=workspace, one=one, two=two, first=first, second=second, fork_point=fork_point)
