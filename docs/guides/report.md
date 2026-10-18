# See how an agent evolved

```sh
thearc report artifacts/graphify -o report.html --reflections artifacts/reflections
```

Runs, candidates, merges and releases come from the workspace's branches —
`candidate/<run>/<name>`, `merge/<id>`, `release/<version>` (and `curation/<id>`) — and the journal: a
merge's entries name the candidates it combined.

`report.html` is one self-contained file: open it in a browser, keep it with the run, or share it.
It combines every branch of the workspace (ratings recorded on one, a curator's changes on
another) and follows sections across renames by their lineage ID.

| View | Shows |
| --- | --- |
| **Evolution graph** | The workspace's history as the agent's evolution, newest first: each change commit a row (section changed, reason, branch, author), candidates as lanes forking from their base, merges joined to the candidates they drew on, releases as tags. The merged-from edges are dashed: they come from the journal, since git records a merge as a line from the base. Bookkeeping commits and dead branches can be shown or hidden. Select a row for its reason, diff, and — for a merger commit — the candidate commits it drew on. A run that forked from an older version than the latest release is flagged. |
| **Section versions** | One section's text at the base, in each candidate (new words in bold), and merged — coloured by where each phrase came from: a candidate, several, or the merger itself. |
| **Changes** | Every committed change per branch, with its reason and diff. |
| **Ratings** | A grid of every section against every revision, coloured by its ratings there (red harmful, green helpful) and marked with what happened (✎ modified, → moved, + added, ✕ removed, ✓ kept). Select a section for its history: each change with its reason, commit, and diff, and each rating with the reflection that gave it. |
| **Documents** | Any configuration file at any revision, compared with another, with each heading's ratings beside it. |

From Python, with your scales for exact label order and meaning:

```python
from thearc.resources.report import write_report
from thearc.resources.evolution import evolution

write_report("artifacts/graphify", "report.html", scales=[USEFULNESS], reflections="artifacts/reflections")
data = evolution("artifacts/graphify")   # the same data as JSON, for your own tools
```
