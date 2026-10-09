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

Run any command with `--help` for every option.
