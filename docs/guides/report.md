# See how an agent evolved

```sh
thearc report artifacts/graphify -o report.html --reflections artifacts/reflections
```

`report.html` is one self-contained file: open it in a browser, keep it with the run, or share it.
It combines every branch of the workspace (ratings recorded on one, a curator's changes on
another) and follows sections across renames by their lineage ID.

| View | Shows |
| --- | --- |
| **Sections** | A grid of every section against every revision, coloured by its ratings there (red harmful, green helpful) and marked with what happened (✎ modified, → moved, + added, ✕ removed, ✓ kept). Select a section for its history: each change with its reason, commit, and diff, and each rating with the reflection that gave it. |
| **Timeline** | Each branch's commits; blue dots changed the configuration. |
| **Documents** | Any configuration file at any revision, compared with another, with each heading's ratings beside it. |

From Python, with your scales for exact label order and meaning:

```python
from thearc.resources.report import write_report
from thearc.resources.evolution import evolution

write_report("artifacts/graphify", "report.html", scales=[USEFULNESS], reflections="artifacts/reflections")
data = evolution("artifacts/graphify")   # the same data as JSON, for your own tools
```
