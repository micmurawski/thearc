# Cookbook

Runnable scripts in `cookbook/`, each a complete pipeline on the Graphify configuration in
`tests/fastapi` and the training sessions under `logs/`. They make real model calls.

| Script | What it does |
| --- | --- |
| `cookbook/live_reflect.py` | Imports the Graphify configuration (each harness's own hooks included) into a workspace, then reflects on sessions that ran Graphify with the `Lesson` schema and records the ratings. |
| `cookbook/live_ace.py` | One ACE round: the same reflections, then one curation over all of them, ending with the configuration diff. |
| `cookbook/live_ace_with_merge.py` | Parallel pipelines: each in its own worktree, reflecting on its own sessions and curating; a merger combines the candidates and the run ends with the diff from the base. |

```sh
.venv/bin/python cookbook/live_ace_with_merge.py
```

Results go under `artifacts/live-reflection/`: the workspace (`graphify/`), the session index,
saved reflections, and — for merged runs — one directory per run with its worktrees.
