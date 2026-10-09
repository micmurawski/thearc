# Contributing

```sh
python -m pip install -e '.[mcp,codex,dev]'
./lint.sh                 # ruff and mypy on thearc/
python -m pytest -q       # tests/ ; the Graphify fixtures need the tests/fastapi submodule
```

Tests make no model calls: scripted runtimes stand in for reflectors, curators, and mergers, and
call the real MCP tools. Live checks are the cookbook scripts.

## Where things are defined

- `GLOSSARY.md` defines every concept — MetaAgent, journal, markers, reflection, curation,
  merging — and is updated together with the code.
- `PARKED_PROBLEMS.md` lists questions deliberately left open.

## Layout

| Package | Holds |
| --- | --- |
| `thearc.resources` | MetaAgent, resources, workspace, journal, ratings, markers, git, installation |
| `thearc.sessions` | Session index, adapters, trajectories, export and install, behavioural patterns |
| `thearc.reflection` | Reflector, harness runtimes, the MCP tool server, Markdown rendering |
| `thearc.curation` | Curator and Merger |

`thearc/flow` was carried over from the earlier package and is not yet typed or linted.
