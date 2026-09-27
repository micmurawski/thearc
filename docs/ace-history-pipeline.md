# ACE adaptation of MetaAgent

ACE reads session evidence, proposes changes to a MetaAgent, and returns a
candidate for evaluation. The deterministic toy demonstrates the workflow;
a production reflector/curator and task executor are supplied by the caller.

## One execution path

`AcePipeline.run()` executes `build_ace_flow()`. The toy runner and visualization
use that same flow. It selects and materializes sessions, groups them for
reflection, then processes curation batches in order.

The pipeline owns one candidate copied from the input. Reflectors receive
copies without ranks; curators receive copies of the current candidate.
`apply_curations()` validates, applies, and records each proposed change once.
Later batches see accepted edits only. Rejected IDs and explanations appear
in the result. The original agent is preserved.

```python
from functools import partial
from thearc import MetaAgent
from thearc.learning import (
    AcePipeline, ChangeJournal, HistorySessionSelector,
    HistorySessionMaterializer, ToyReflector, ToyCurator,
)

agent = MetaAgent.from_project("codex", "tests/fastapi")
journal = ChangeJournal("changes.jsonl")
# history is an open HistoryService with indexed sessions.
pipeline = AcePipeline(
    HistorySessionSelector(history),
    HistorySessionMaterializer(history),
    ToyReflector(),
    ToyCurator(),
    on_change=partial(journal.record, "example-run"),
)
result = pipeline.run(agent, run_id="example-run", viz_path="ace-flow.html")
candidate = result.agent
print(result.update.applied_curation_ids)
print(result.update.rejected_curation_ids)
print(result.update.warnings)
```

Recording is optional. The recorder receives `(curation, before, after)`;
recorder failures abort the run. The toy journal stores addresses, operations,
evidence, rationale, timestamps, and hashes of before/after values.
Persistence remains explicit through `candidate.install(...)`.

## Collections and addresses

MetaAgent stores typed dictionaries: `skills`, `hooks`, `mcps`, `context`,
and `resources`. Constructors accept either named mappings or lists.
Iteration follows normal dictionary semantics; use `.values()` for objects.

```python
agent.skills["graphify"]
agent.context["AGENTS.md"]
agent.resources["rules/graphify.md"]
```

All addressing methods take `ResourceTarget`: `read_target(target)`,
`rank_for(target)`, `apply_rank_delta(target, delta)`, and
`apply_change(target, operation, value)`.

| Kind | Name | Optional section |
| --- | --- | --- |
| skill | Skill name | Heading in instructions |
| skill_file | skill/relative/file.md | Heading in bundled file |
| context | Context filename | Heading in document |
| hook | Hook name | No |
| mcp | MCP name | No |
| rule, workflow, command | Relative file path | Heading in content |

MCPs support configuration changes but do not carry ranks. Section names must
match a unique heading. File targets must stay within their resource directory.

## One change model

`Curation` holds an ID, `ResourceTarget`, `Operation.ADD/EDIT/REMOVE`, value,
evidence/reflection IDs, and rationale. The application path requires a
non-blank rationale. ADD requires an absent target; EDIT and REMOVE require an
existing target. EDIT takes a field patch for models or replacement text for
bundled files. A section edit accepts MarkdownSection fields. REMOVE has no value.

```python
from thearc import Operation, ResourceTarget
from thearc.learning import Curation, apply_curations

change = Curation(
    id="query-guidance",
    target=ResourceTarget(kind="skill_file", name="graphify/references/query.md"),
    operation=Operation.EDIT,
    value="# Query\n\nUpdated guidance.\n",
    reflection_ids=["reflection-1"],
    rationale="Clarify the query workflow using session evidence.",
)
candidate = agent.model_copy(deep=True)
update = apply_curations(candidate, [change])
```

`apply_curations` mutates its explicit candidate argument. The pipeline handles
copying its input before calling it.

## Evaluation

Supply a callback that executes one held-out task and returns a `TaskScore`.
The callback owns the harness, success criteria, cost units, and timing.
No model calls or installations occur unless the callback performs them.

```python
from thearc.learning import TaskScore

def evaluate(agent, task):
    measured = execute_task(agent, task)  # Your harness.
    return TaskScore(
        success=measured.success,
        cost=measured.cost,
        latency_seconds=measured.latency_seconds,
    )

result = pipeline.run(
    agent,
    evaluation_tasks={"held-out-task-1": task},
    evaluator=evaluate,
)
print(result.evaluation.summary)
```

`compare_agents(original, candidate, tasks, evaluate)` is also available
independently. It evaluates both agents on identical tasks, alternates execution
order, and isolates each callback with copies. The report retains each task's
outcome and summarizes success rate, mean cost, and mean latency, with deltas.

Evaluation IDs must be disjoint from adaptation session IDs. Callers must use a
consistent ID namespace and exclude semantically duplicated tasks themselves.
Evaluation failures propagate instead of being counted as failed agent tasks.
Metrics expose tradeoffs; they do not automatically select or install a winner.

## Demos and limits

Run `scripts/ace_toy_demo.py DIRECTORY --no-viz` for synthetic session evidence,
or `scripts/ace_fastapi_sessions_demo.py` for the previously collected FastAPI
sessions. The FastAPI example saves before/after MetaAgents, a journal, lifecycle
records, a checkpoint, and the flow visualization in a fresh run directory.

The toy reflector counts outcomes and proposes ranks; those counts are not a
measurement of improved task performance. Evaluation needs a real task executor.
The pipeline still needs model adapters, relevance-aware evidence truncation,
durable intermediate resume, and promotion policy for a production workflow.
AceQuery time/tag fields remain reserved; only its SearchFilters are currently
forwarded to history search.

## API migration

Removed collection wrappers (`SkillSet`, `HookSet`, `MCPSet`, `ContextSet`,
`AgentResourceSet`), the `AceContext` alias, unused change contracts
(`ChangeIntent`, `ChangeSet`, `CommitResult`), and the separate updater
classes. Use MetaAgent dictionaries, Curation, and `apply_curations`.
AcePipeline no longer takes an updater; pass an optional `on_change` callback
for journaling. Previously saved wrapper-shaped agent JSON must be flattened
to the new dictionary schema before loading.
