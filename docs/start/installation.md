# Install

`thearc` needs Python 3.11 or newer.

```sh
python -m pip install -e '.[mcp,codex]'
```

| Extra | For |
| --- | --- |
| `mcp` | Reflection, curation, and merging: the agents hand in their work through MCP tools served for each run. |
| `codex` | Codex as reflector, curator, or merger (the `openai-codex` SDK). |
| `docs` | Building this site. |
| `dev` | Tests, `ruff`, and `mypy`. |

Agents run in the harnesses you already use. Install and sign in to the ones you want as
reflector, curator, or merger — `codex`, `claude`, or `pi` — and keep them on your `PATH`. See
[runtime support](../reference/runtimes.md) for what each harness can do.

Check the command line:

```sh
thearc --help
thearc sessions --help
```
