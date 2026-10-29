# Evolve a project with /evolve

The **evolve skill** lets the people working in a project evolve its skills and instructions for one harness from
the sessions they have already run there. Typing `/evolve` in the agent runs a **round**: thearc reads the
project's recent sessions of that harness, reflects on each, and curates the configuration from the reflections
into a **proposal**, a new version that is not installed yet. You review the diff, ask the agent for amendments,
and apply it.

## Set a project up

The project must be a git repository.

```bash
thearc skills install self-evolve --project . --harness claude
```

This:

1. **Adopts** the project's configuration for that harness (for Claude: `.claude/skills/*`, `CLAUDE.md`, its hooks
   and MCP servers) as a MetaAgent named after the project, version `1.0.0`.
2. Keeps it in a **workspace** at `.thearc/workspace`: a repository of its own (`main`, `release/…`), added to the
   project as a git submodule, so each project commit pins the workspace commit its installed files came from. With
   `--remote URL` the workspace's `origin` is set and `main` pushed, and the submodule records that URL.
3. Installs the **evolve skill** into the harness's skills folder (`.claude/skills/evolve/`): `SKILL.md`, the recipe
   `evolve.py` and its prompts in `prompts/`. It is not part of the MetaAgent (`agent.json` holds
   `"ignore": ["skill:evolve"]`), so nothing evolves it.

A project evolves one harness: installing for another harness is refused. `--runtime` picks the harness that runs
reflections and curation (default: `--harness`). Run the install again to reinstall the evolve skill.

The workspace is where new versions are made; `.claude/` (or the harness's own folders) holds the installed copy the
harness reads. Commit the installed files, `.gitmodules`, `.thearc/workspace`, `.gitignore` and
`.thearc/installed.json`. A teammate who clones the project with `--recurse-submodules` has the workspace; otherwise
`thearc evolve init` checks it out. `thearc evolve remote URL` gives the workspace a remote later.

## A round

| In the agent | Command line | Does |
| --- | --- | --- |
| `/evolve` | `thearc evolve` | Run the recipe and create the proposal; show what it changes. |
| `/evolve diff` | `thearc evolve diff` | The proposal against the installed version, with who changed what. |
| `/evolve amend` | `thearc evolve amend -m WHY` | Commit the edits made in `.thearc/workspace` to the proposal. |
| `/evolve apply` | `thearc evolve apply [--push]` | Release the proposal and install it; `--push` pushes the workspace. |
| `/evolve discard` | `thearc evolve discard` | Drop the proposal. |
| `/evolve ignore …` | `thearc evolve ignore PATTERN… [--remove]` | Leave targets out of evolution. |
| `/evolve status` | `thearc evolve status` | Version, open proposal, harness, runtime, what is evolved and ignored. |
| | `thearc evolve rollback VERSION` | Install an earlier release again, as a new version. |

Before the recipe runs, thearc checks the installed files for **drift**: edits made by hand (or by another tool)
since the last install are committed to the workspace as your change, so a round never overwrites them.

The default recipe takes the harness's sessions run in the project in the last week (at most 20, never one that ran
`/evolve`), reflects on each against the installed version, and curates the configuration. It needs at least 3
sessions. Its reflections are kept whole in the journal, so a stopped round resumes where it stopped and the report
shows what each reflector said.

Applied changes take effect in the next session: a harness reads its skills when a session starts.

## Amend a proposal

While a proposal is open, `.thearc/workspace` is checked out on `proposal/<version>`. Ask the agent for a
change; it edits the files there (not the installed ones) and runs `thearc evolve amend -m "why"`. The amendment is
checked as a curator's commit is (the journal and `agent.json` stay as they are, new sections get their markers) and
recorded with you as its author.

## Watch a round

A round takes minutes. `thearc evolve` prints `Progress: file://…/.thearc/cache/progress.html` first, and `--open`
opens it (the evolve skill does both). The page shows the round as it runs: its steps as a graph (find the sessions,
reflect on each, curate), which one is running, each session's reflection as it finishes, and the round's log
(`round.log`: the sessions found, each reflection's ratings, the curator's commits) in a tab. It reloads itself until
the round ends; a copy stays in the round's folder, `.thearc/cache/rounds/<round>/`.

## Change the recipe

`evolve.py` and `prompts/reflect.md` and `prompts/curate.md` in the installed evolve skill are plain files: edit them,
and the next round uses them. The recipe is a [flow](../../thearc/flow) of three nodes, run with
`evolution.track(flow, shared)` so it shows on the progress page:

```python
HARNESS = "claude"  # Whose sessions are read: the harness this project evolves (set at install).
RUNTIME = "claude"  # Runs every reflection and the curation.
WINDOW = timedelta(days=7)
MIN_SESSIONS = 3


def propose(evolution: Evolution) -> MetaAgent | None:
    find, too_few, reflect, curate = FindSessions(), TooFew(), Reflect(), Curate()
    find - "too few" >> too_few
    find - "enough" >> reflect >> curate
    shared = evolution.track(Flow(start=find), {"evolution": evolution})
    return shared.get("proposal")
```

- `FindSessions` takes `evolution.sessions(harness=HARNESS, since=WINDOW)` and goes on with at least `MIN_SESSIONS`.
- `Reflect` is a batch node: one `Reflector(RUNTIME).handoff(...)` per session, skipping those a stopped round
  already reflected on (`evolution.reflected`).
- `Curate` runs `Curator(RUNTIME).curate(...)` on `evolution.candidate()` and returns it as the proposal.

Each node writes to `evolution.log`. `evolution.track(flow, shared, logs={"name": path})` adds more log files as tabs.
A recipe may curate several candidates in parallel (`evolution.candidate("a")`, `evolution.candidate("b")`) and
merge them with `Merger`; `evolution.merged()` returns the merge a stopped round made.

## Ignored

`thearc evolve ignore skill:vendor-*` or `"context:CLAUDE.md#Private notes"` leaves those targets out of the
configuration: they are not adopted, evolved or installed, and their files stay as you wrote them. The evolve skill
is ignored this way from the start. In a context document thearc owns top-level sections, so ignore a top-level
section, not a subsection of one.

Patterns are `kind:name[#section]` with `*` wildcards; a pattern names its targets' subsections too, and
`skill:<name>` its skill's files.
