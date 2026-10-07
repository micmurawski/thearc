# More examples

| Recipe | Purpose | Model calls |
| --- | --- | --- |
| `cookbook.codex_example` | Index captured logs, translate history, submit reflections through MCP | Unless `--prepare-only` |
| `cookbook/resource_ace.py` | Reflection, validated assessments, and curation | When `adapt()` is called |
| `cookbook.graphify_evidence` | Filter indexed sessions and reflect on behavioral matches | Only when patterns match |
| `cookbook.graphify_handoff` | Reflect through handoff and curate Graphify | Yes |
| `cookbook.graphify_parallel` | Parallel candidates and conflict-aware merging | Yes |
| `cookbook.snapshot_sessions` | Save portable selected evidence | No |
| `cookbook.inspect_evidence` | Search and inspect saved evidence | No |
| `cookbook.prepare_handoff` | Save a task/context plan | No |
| `cookbook.async_flow` | General asynchronous flow composition | No |

The evidence recipe is a Python function; setup is shown in the guide below.
Run the command-line recipes from the repository root with `python -m cookbook.MODULE --help`.
See [the first ACE run](../start/first-ace.md) and [resource interface](../guides/resources.md).
For three complete recipes against collected results, see [Graphify examples](graphify.md).
For a logs-to-JSON pipeline using Codex and MCP, see [Codex reflections](codex.md).
