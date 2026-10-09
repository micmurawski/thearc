# Index and select sessions

Sessions are read from each harness's transcripts into a local SQLite index.

```sh
thearc sessions --index sessions.sqlite add logs/codex-training
thearc sessions --index sessions.sqlite list --harness codex
thearc sessions --index sessions.sqlite trajectories <session>
thearc sessions --index sessions.sqlite render <session> --trajectory 0
```

The same from Python:

```python
from thearc.sessions import Sessions

with Sessions("sessions.sqlite") as sessions:
    source = sessions.add("logs/claude-training")            # detects the harness, synchronizes
    records = sessions.list(source=[source.id])               # newest first
    session = sessions.get(records[0].id)                     # any ID, native ID, or unique prefix
```

## Trajectories

A **trajectory** is the agent's response to one user prompt. `session.trajectories` splits a
session at its prompts; `session.through(trajectory)` is the session up to and including one.

## Finding sessions

```python
from thearc.sessions.scanner import ShellCommand, SkillUsage

hits = sessions.search("SyntaxError", kind="tool_result", status="failure")
used = {m.session_id for m in sessions.match(ShellCommand(contains="graphify query"))}
records = [r for r in sessions.list() if r.id in used]
```

`match` runs behavioural patterns over complete event sequences (loops, abandoned fixes, ignored
instructions, shell commands, skill usage). A match is a signal to inspect, not proof of a cause.

## Export and install

`session.export("codex")` converts a session to another harness's transcript format;
`session.install("claude")` writes a copy that harness can resume or fork. Reflection's handoff
uses this to start a reflector from a session's history.
