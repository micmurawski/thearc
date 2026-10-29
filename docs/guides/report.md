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
| **Ratings** | A grid of every section against every revision, coloured by its ratings there (in the scale's colours) and marked with what happened (✎ modified, → moved, + added, ✕ removed, ✓ kept). Select a section for its history: a chart, then each change with its reason, commit, and diff, and each rating with the reflection that gave it. |
| **Documents** | Any configuration file at any revision, compared with another, with each heading's ratings beside it. |

From Python, with your scales for exact label order and meaning:

```python
from thearc.resources.report import write_report
from thearc.resources.evolution import evolution

write_report("artifacts/graphify", "report.html", scales=[USEFULNESS], reflections="artifacts/reflections")
data = evolution("artifacts/graphify")   # the same data as JSON, for your own tools
```

## A section's chart

Selecting a section shows its ratings and changes over the rounds that judged it, three ways:

- **Rounds**: a bar per round, with a lane underneath for the changes that round made (a dashed one was never
  released) and a ✓ when the round kept the section as it was.
- **Versions**: the rounds sit in bands for the version they judged, and the changes that made the next version
  are numbered gates between the bands, listed with their reasons below. A release no round has judged yet shows
  as an empty band.
- **Score**: each round as a dot at its mean rating, sized by its number of ratings, with the score before and
  after each gate.

On a diverging scale (harmful … neutral … helpful) good labels rise above the baseline and bad ones hang below it;
on a sequential one (meaningless … critical) every label rises. A scale's labels take their order and colours from
the scale (see [Reflections](reflections.md)). The report remembers the view you chose.
