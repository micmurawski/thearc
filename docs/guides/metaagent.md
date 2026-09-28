# Manage configuration

A `MetaAgent` is a portable configuration, not a running agent. It holds named
collections of skills, hooks, MCP servers, context documents, and agent-local
rules, workflows, and commands. Skills include their bundled files, such as
`references/query.md` and upstream version markers.

## Import or create

```python
from thearc import MetaAgent, Skill

# Import an existing installation. Replace IMPLEMENTATION and the project path.
agent = MetaAgent.from_project(
    "IMPLEMENTATION", "/path/to/project", name="my-agent",
)

# Or construct a configuration in Python.
agent = MetaAgent(
    name="my-agent",
    version="1.0.0",
    skills={"review": Skill(name="review", instructions="Read relevant tests before editing.")},
)
```

Implementations are `antigravity`, `claude`, `codex`, and `pi`. Direct construction
requires a name. Import can discover installed identity; specifying `name=` makes
your intended configuration identity explicit. Importing today's configuration
does not prove that it was loaded during a historical session.

## Save and compare

```python
agent.to_workspace("artifacts/my-agent")
restored = MetaAgent.from_workspace("artifacts/my-agent")
print(agent.diff(restored))  # Empty when there are no differences.
```

A canonical workspace stores `agent.json` and editable resource files. It is
different from a harness installation. Use `before.diff(after)` to inspect changes;
`include_ranks=True` includes assessment counters and `include_version=False` hides
the top-level release label. Diffs can contain sensitive configuration text.

## Names and versions

Names are nonempty, safe filename components. A version is an optional release
label, not an automatically incremented number or proof of exact content.
`MetaAgent(name="graphify", version="1.0.0")` writes `.graphify_version` alongside
canonical metadata and in the target harness base directory when installed.

Installed `.thearc-agent.json` records identity. A nested
`skills/graphify/.graphify_version` belongs to the upstream skill and is preserved
independently of the complete MetaAgent version. Use content hashes and curation
history for exact provenance. Set a release version explicitly when publishing.

## Install configuration

```python
for implementation in ("antigravity", "claude", "codex", "pi"):
    agent.install(implementation, path=f"artifacts/install/{implementation}")
```

Each path above is a separate project root. Installation writes configuration,
not agent executables. Matching files are overwritten by default; review the
destination before targeting a real project. Conversion uses each harness's
installer, but does not prove identical runtime behavior across harnesses.

`replace=False` requests merge behavior and records an unknown installed release
version, because the combined result is not necessarily the incoming release.
Installation is not an atomic transaction across all generated files.
