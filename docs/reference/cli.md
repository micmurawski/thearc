# CLI reference

Use `thearc --help` and `thearc GROUP COMMAND --help` for complete options.
Commands are grouped by the object they work with:

| Group | Commands | Calls a model? |
| --- | --- | --- |
| `sessions` | `index`, `sync`, `list`, `search`, `show`, `trace` | No |
| `evidence` | `snapshot`, `export`, `inspect` | No |
| `reflection` | `run`, `handoff`, `show` | `run --execute` or `handoff --execute` |
| `curation` | `run`, `show`, `history`, `diff` | `run` does |
| `handoff` | `prepare`, `reflect`, `show` | `reflect --execute` only |

`handoff reflect` aliases `reflection handoff`. Both continue a native fork.
`handoff prepare/show` save and read offline plans; `reflection show` reads
reflection artifacts, including native handoff results.

## Index placement

The session and evidence groups take `--index` **before** the subcommand:

```sh
thearc sessions --index artifacts/sessions.sqlite list
thearc evidence --index artifacts/sessions.sqlite snapshot \
  --session-id CANONICAL_ID --output artifacts/evidence
```

Repeat `--session-id` to select several sessions. Date-based snapshot selection is
available through the [Python filter API](../guides/sessions.md#select-by-date-or-source).
Reflection's `--index` belongs to its `run` command instead.

## Read saved artifacts

```sh
thearc evidence export --snapshot artifacts/evidence \
  --format markdown --output artifacts/readable-evidence
thearc evidence inspect --snapshot artifacts/evidence \
  --tool search_events --arguments '{"query":"graphify"}'
thearc reflection show artifacts/reflections
thearc curation diff artifacts/curation
thearc handoff show artifacts/handoff
```

These commands do not replay sessions or invoke agents. `inspect` executes one
local retrieval operation, not an agent inspection.

For complete workflows, use the [session](../guides/sessions.md),
[reflection](../guides/reflections.md), and [curation](../guides/curation.md) guides.
Older hidden aliases remain for compatibility; new integrations should use these
object-oriented groups.
