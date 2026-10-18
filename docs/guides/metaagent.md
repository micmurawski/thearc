# Manage configuration

A **MetaAgent** is a named, versioned agent configuration: skills (with their files), context
documents such as `AGENTS.md`, rules, workflows, commands, hooks, MCP servers, and harness-only
extensions. It also carries a **journal** — the history of every section.

## Import, edit, install

```python
from thearc.resources import Hook, MetaAgent, Skill

agent = MetaAgent.from_installation("claude", "path/to/project", name="graphify", version="1.0")
agent.skills["retry"] = Skill(name="retry", description="Retry failing commands", instructions="# Retry\n…")
agent.install("codex", "path/to/other-project")
```

Installation writes the configuration in each harness's own layout and records what it owns in
a marker file, so reinstalling replaces exactly that. Context documents such as `CLAUDE.md` are
merged by section, keeping the project's own sections.

**Hooks per harness.** Matchers name a harness's own tools, so hooks often differ per harness:
`Hook(…, harnesses=["claude"])` installs only into Claude. **Owned hooks** —
`agent.owns_hooks = ["graphify *"]` — mark hook commands as the agent's wherever they appear: every
install replaces them, including ones installed by hand, instead of adding copies.

## Revisions and diffs

`agent.revision` is `version@rev_…`: the version plus a hash of the configuration files. A
journal entry records the revision it applies to. `agent.diff(other)` is a unified diff of two
configurations.

## Workspaces

```python
agent = agent.init_workspace("artifacts/graphify")   # git init, initial commit, journal seeded
agent = MetaAgent.from_workspace("artifacts/graphify")
light = MetaAgent.from_workspace("artifacts/graphify", journal=False)   # configuration only
```

An agent read from or initialized in a workspace remembers it (`agent.workspace`).
`add_reflection` then also appends to `journal.jsonl`, and `release` and `to_worktree` act on that
repository; each first checks that nobody edited the files since the agent was read.

A workspace is the configuration as files plus `agent.json` (name, version) and
`journal.jsonl`. Files carry **markers** — each section's lineage ID:

```markdown
## Limits <!-- res_fd94… -->
```

Ratings stay in the journal; `agent_outline(agent, USEFULNESS, ids=True)` shows them per section.

Markers are stripped when a workspace is read, so revisions, installations, and the files
reflectors read never contain them. While someone edits, a marker says which section is which:
a heading that keeps its marker under a new title is a move, not a removal plus an addition.

## The journal

The journal is append-only. It holds **ratings** (from reflections) and **changes** (Added,
Modified, Moved, Removed — from commits, each with the commit message as its reason).

```python
from thearc.resources import record_commits, agent_at

record_commits("artifacts/graphify", agent.journal)        # commits not yet in the journal
agent.journal.changes(target, subsections=True)            # a section's change log, across moves
agent.journal.summaries(USEFULNESS)                        # rating summary per section
before = agent_at("artifacts/graphify", "HEAD~3")          # the configuration at any commit
```

Rating policies decide which ratings count: `count_all`, `since_last_modification` (ratings of a
section's current text), and `including_subsections(policy)` (a parent includes its subsections).
`agent_outline(agent, USEFULNESS, ids=True)` prints every section with its ID and summary.
