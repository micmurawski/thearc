# CLI

```text
thearc sessions [--index PATH] COMMAND      (alias: thearc s)
```

The index defaults to `~/.thearc/learning.sqlite`.

| Command | Does |
| --- | --- |
| `add PATHS… [--harness H] [--format native\|dataclaw] [--source-id ID] [--no-sync]` | Register transcript directories and synchronize them. |
| `sources` | Show registered sources. |
| `sync [--source-id ID]` | Read new and changed transcripts. |
| `list [--harness H] [--since T] [--until T] [--json]` | Indexed sessions, newest first. |
| `search [TEXT] [--mode literal\|lexical] [--kind K] [--tool T] [--status S] [--json]` | Events matching text and filters. |
| `show EVENT_ID [--context N]` | An event with its surrounding events. |
| `trajectories SESSION` (alias `t`) | A session's trajectories. |
| `render [SESSION] [--trajectory N] [--through] [--format F] [--best-effort]` | Markdown, or a harness's transcript format. |
| `install [REFERENCES…] --to H [--trajectory N] [--through] [--all]` | Install sessions where a harness can resume or fork them. |
| `trace RUN` | A run's sub-agent runs and delegations. |

```text
thearc report WORKSPACE [-o report.html] [--ref BRANCH …] [--reflections DIR]
```

Writes the [evolution report](../guides/report.md) of a workspace.

```text
thearc agent install --repo URL|PATH --harness H [--version VERSION] [--project DIR | --global] [--force]
thearc agent uninstall --name NAME --harness H [--project DIR | --global]
```

Installs a MetaAgent from its workspace repository (`--repo`, or its spellings `--url` and `--path`: a git URL is
cloned to a temporary folder), at the release `--version` names (its `release/<version>` branch; default `main`).
`--project` (the default, with the current folder) installs into the harness's project layout; `--global` into the
harness's own folder for every project: `~/.claude` (or `$CLAUDE_CONFIG_DIR`), `~/.codex` (or `$CODEX_HOME`),
`~/.pi/agent`, `~/.config/opencode`. The install record goes beside what was installed, so reinstalling replaces
exactly what the last install wrote. A global install for Claude skips MCP servers (Claude keeps them in
`~/.claude.json`), and hook scripts run by their absolute path. `--force` overwrites files the agent's earlier
installs did not write.

```text
thearc skills install self-evolve [--project DIR] --harness H [--runtime H] [--ignore PATTERN …] [--remote URL]
thearc skills uninstall self-evolve [--project DIR]
thearc evolve [--project DIR] [--json] [--recipe FILE] [--open]
thearc evolve diff | amend -m WHY | apply [--push] | discard | status | ignore PATTERN… [--remove] | rollback VERSION
thearc evolve init | remote URL
```

Set a project up for [/evolve](../guides/evolve-skill.md), and run its rounds.

Run any command with `--help` for every option.
