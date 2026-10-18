# Merge candidates

Run several pipelines in parallel from the same base, then combine their candidates.

```python
from thearc.curation import Curator, Merger

c1 = agent.to_worktree("runs/1/p1")       # branch candidate/p1, from the workspace's current commit
c2 = agent.to_worktree("runs/1/p2")
for candidate, reflections in ((c1, r1), (c2, r2)):
    for reflection in reflections:
        candidate.add_reflection(reflection)
    Curator("codex").curate(PROMPT, reflections, candidate)   # moves the worktree forward

merge = Merger("codex").merge(MERGE_PROMPT, [c1, c2], into="runs/1/merged")
merge.agent      # the merged candidate, in its own worktree
merge.proposals      # what the merger decided on
merge.commits        # one per section
```

## How merging works

- thearc finds the candidates' shared base from git and refuses candidates of different
  workspaces, or ones that did not all branch from the same commit.
- It gathers the **proposals**: every section any candidate changed, grouped by lineage ID, with
  its text at the base, each candidate's version, the commit messages behind it, and the ratings
  that candidate's pipeline gave the section.
- The merger decides each section's final shape — take one version, combine them, rewrite, or
  keep the base — in a clone at the base, **one commit per section**, naming the candidates and
  commits it drew on.

The merged journal holds the base journal, every candidate's ratings, and the merger's own
changes; the curators' change entries stay on their branches. See `PARKED_PROBLEMS.md` for the
open question about how much candidate history a merge should keep.

## Releasing the merged candidate

```python
merge.agent.release()                   # 0.9.68 -> 0.9.69, on new branch release/0.9.69
merge.agent.release(part="minor")       # or "major", or version="1.0.0"
merge.agent.release(include_scales=True, push="origin")   # RATINGS.md in the release; push the branch
```

Only the version in `agent.json` changes, in a commit of its own, and a branch `release/<version>`
points at it. Your `main` branch is left for you to move, push, or install from.

