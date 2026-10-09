# Curate changes

A **curator** edits the configuration in response to reflections, like acting on a code review.

```python
from thearc.curation import Curator

curation = Curator("codex").curate(PROMPT, reflections, agent, scales=[usefulness])
curation.commits      # one per change: sha, message (the reason), targets changed
curation.entries      # the journal entries recording them
curation.candidate()  # the curated configuration
```

The reflections must already be in the agent's journal (`agent.add_reflection`). With `scales`,
the workspace's markers are refreshed first, so the curator sees every section's ratings next to
its text.

## How the curator works

- It edits a clone of the workspace at the current commit — the **base** — and runs no commands.
- It commits through a `commit_change(message)` tool: **one change per commit, the reason as the
  message**. The tool refuses edits to `journal.jsonl` or `agent.json`, a workspace that no longer
  reads back, and an empty message.
- It ends with `finish(summary)`, which refuses while edits are uncommitted.

The prompt shows each reflection as Markdown, followed by its ratings with the IDs and current
addresses of the sections they landed on.

## The result

The commits after the base are recorded in the journal — Added, Modified, Moved, or Removed per
changed section, with the commit message as the reason and the IDs of every section that commit
touched. A curation that changes nothing records **Kept** entries with the curator's reason.

The candidate lands on branch `curation/<id>` and your branch is left as it was — unless the agent
is a worktree (see [merging](merging.md)), which then moves forward to the candidate.
