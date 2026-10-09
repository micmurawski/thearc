# Runtime support

Reflectors, curators, and mergers run in the harnesses you already use. thearc connects its
tools (`submit_reflection`, or `commit_change` and `finish`) to each run as an MCP server served
from your own process, and turns off your own customizations — hooks, skills, context files —
where the harness allows it.

| Harness | Reflect | Hand off a session | Curate / merge | How |
| --- | --- | --- | --- | --- |
| Codex | yes | yes | yes | `openai-codex` SDK; read-only sandbox to reflect, `workspace-write` to edit |
| Claude Code | yes | yes | yes | `claude -p`; Read/Grep/Glob, plus Edit/Write to edit |
| Pi | yes | yes | yes | `pi -p --mode json`; read/grep/find/ls, plus edit/write; tools through a run-only extension |
| Antigravity | no | no | no | reads MCP servers only from your own configuration, so thearc cannot connect its tools for one run |

Sessions can be indexed from Claude Code, Codex, Pi, OpenCode, and Antigravity, and exported to
any of their formats; `--best-effort` keeps what a format cannot represent as labelled text.

Notes:

- Agents never run commands while curating or merging; every commit goes through
  `commit_change`.
- Handed-off sessions are installed as copies in the harness's home (or `home=`); the original
  transcript is never changed.
