# Glossary

Shared definitions for `thearc`. These describe the domain first and identify
current Python types where useful. A concept can exist without a dedicated class.

## The learning cycle

```text
Agent execution → Session / Trajectory → Evidence → Reflection
                                                    ↓
Baseline MetaAgent + Reflections → Curation → Candidate MetaAgent
                                                    ↓
                                   Evaluation against the baseline
```

Reflection produces findings. Curation turns findings into configuration changes.
Evaluation measures the candidate's behavior. Installation makes a configuration
available to an agent harness. Each is a separate step.

## Agents and configuration

### Agent

An executing system that uses a model, instructions, tools, and conversation
context to perform a task. Its execution produces events. The original agent,
reflector, and curator are roles that may use different models or runtimes.

### MetaAgent

A named, optionally versioned configuration of an agent's skills, hooks, context
documents, MCP connections, and other resources. It can be inspected, edited,
compared, saved, and installed into supported harnesses.

`MetaAgent` represents configuration; the runtime executes the agent using it.
A saved configuration does not establish which instructions were loaded in a
historical session.

### Resource and resource target

A **resource** is a configurable part of a MetaAgent. A **resource target** is its
address, optionally narrowed to a Markdown section. `ResourceTarget` identifies
the part being assessed or changed.

| Resource | Meaning |
| --- | --- |
| `Skill` | Reusable instructions, metadata, and supporting files for a capability. |
| Skill file | A supporting file addressed within a skill. |
| `ContextDocument` | Instructions or context supplied through a document such as `AGENTS.md`. |
| `Hook` | Configuration for an action triggered by a harness event. |
| `MCP` | Configuration for a connection to a Model Context Protocol server. |
| `AgentResource` | An additional configuration resource stored in an agent directory. |

### Harness, runtime, backend, and runner

- **Harness:** the agent environment that manages conversation, tools, and
  configuration conventions. A source harness also determines the native log format.
- **Runtime:** the executable CLI or SDK used to run an agent.
- **Backend:** `thearc`'s integration with a runtime. `ReflectionBackend` records
  its runtime identity, adapter name, and version.
- **Runner:** the callable that performs an execution through that integration
  and returns its response and execution metadata.

The source harness, reflection backend, and installation target can differ in
evidence-based reflection. Native handoff requires a runtime capable of forking
the source conversation.

Source: [configuration models](thearc/models/agent.py),
[execution contracts](thearc/learning/reflection/backend.py).

## Sessions and behavior

### Event

One normalized record of agent activity: for example, a message, tool call, tool
result, or metadata record. An indexed `Event` carries its content, relationships,
and a reference to the original record. `event_id` is sufficient to locate its
indexed session and provenance.

### Session

A recorded conversation and its associated events. A session can contain several
execution runs, including delegated work. The indexed `Session` object holds
identity and summary metadata; its events are stored and retrieved separately.

### Trajectory

The ordered chain of events triggered by one user prompt, ending at completion or
interruption. It includes recorded assistant text, thoughts, tool calls, tool
results, and other execution events. The triggering prompt is stored separately.

```text
S  = (p0, t0, p1, t1, ..., pk, tk)
ti = (ei0, ei1, ..., eiN)
```

A **session** contains successive prompt–trajectory pairs. A **trajectory** is the
response activity for one prompt. A **turn** is the pair `(pi, ti)`. Even a session
with one agent can contain many trajectories. One prompt may contain several
native content blocks.

`Trajectory` stores `prompt_events`, response `events`, and the observed boundary.
`split_trajectories()` uses normalized user messages and explicit lifecycle markers.
When completion is not recorded, the next prompt or end of the available stream
bounds the extracted trajectory without establishing successful completion.
Events preceding a prompt or outside its response remain in
`TrajectorySplit.unassigned_events`. Different runs and artifacts are split
independently because their ordering does not establish causal relationships.

### Trajectory segment

A bounded portion of a trajectory selected for inspection. A sequence pattern
match identifies such a segment. A segment provides local evidence; broader
context may be needed to interpret it.

### Run

An execution context within an indexed session. The `Run` model groups events and
can identify a parent run for delegated work.

An **adaptation run** is different in scope: one execution of the learning pipeline,
potentially involving many sessions, reflections, and changes.

### Task, episode, and outcome

- **Task:** the objective given to an agent.
- **Episode:** a learning unit assembled around a task or related activity. An
  `Episode` may include events from multiple sessions or runs.
- **Outcome:** the observed result of that activity, optionally with scores and
  supporting signals. `EpisodeOutcome` records success, failure, mixed, or unknown.
  A failed tool call alone does not establish the outcome of the whole task.

### Source, native ID, and canonical ID

A **source** is a configured collection of native logs. `SourceConfig` identifies
its root and format. A **native ID** belongs to the original runtime. A
**canonical ID** belongs to the index and is scoped to its source. APIs that fork
native sessions require native IDs; indexed queries use canonical IDs.

`SessionStore` is the local SQLite index and query service. `HistoryService` is
a compatibility name for the same implementation.

Source: [session models](thearc/learning/sessions/models.py),
[episode contracts](thearc/learning/runtime/contracts.py).

## Evidence and selection

### Evidence

Recorded information available to a reflector or evaluator to support a finding.
It can be supplied as selected events or inherited through a native session fork.
Evidence describes what was recorded; its coverage determines what can be assessed.

### Evidence snapshot and session bundle

- **`EvidenceSnapshot`:** a portable, integrity-checked capture of selected indexed
  sessions and events, with provenance and coverage metadata.
- **`SessionBundle`:** a session's metadata together with selected events and
  lists of omitted or truncated events, prepared for evidence-based reflection.

### Evidence view

The representation of supplied evidence shown to the reflector. **Compact** is
the default: event content, event IDs, relevant relationships, and coverage counts.
**Detailed** adds provenance and audit context. Both exclude source-harness labels;
choosing detailed does not select additional events.

### Coverage, omission, truncation, and selection policy

- **Coverage:** what portion of the available evidence was delivered.
- **Omitted event:** an event excluded from the supplied selection.
- **Truncated event:** an included event whose content was shortened.
- **Selection policy:** the rule used to choose events under a limit.
  `SELECTION_POLICY` is descriptive text about that rule; the selection function
  performs the actual selection.

### Behavioral pattern, pattern scanner, and pattern match

- **`BehavioralPattern`:** a Python matcher that detects a specified sequence or
  condition in ordered events.
- **`PatternScanner`:** selects candidate sessions using SQLite filters, then
  matches their complete event sequences in Python, respecting stream boundaries.
- **`PatternMatch`:** one occurrence, including the exact event slice, positions,
  event IDs, pattern name, and contextual details.

A match is an observed signal for inspection. Its name or severity does not by
itself establish a cause or prove that the behavior was harmful.

Source: [evidence snapshot](thearc/learning/evidence/snapshot.py),
[evidence views](thearc/learning/reflection/evidence.py),
[patterns](thearc/learning/sessions/patterns.py),
[scanner](thearc/learning/sessions/scanner.py).

## Reflection and handoff

### Reflection

The response produced by a **reflector agent** after inspecting evidence under a
reflection prompt. It contains findings, interpretations, suggested improvements,
and limitations grounded in that evidence.

```text
Reflection = AR(RP + Evidence)
```

Here `AR` is the reflector agent and `RP` is the reflection prompt. The equation
describes the inputs and output; evidence can be attached or already in context.
**Reflection is the reflector's response, not its entire session.**

The word **reflection process** refers to invoking the reflector and collecting
that response. In the current implementation:

- Evidence-based reflection produces a structured `Reflection` artifact.
- Native handoff stores the response text in `HandoffReflection.reflection`,
  alongside metadata locating the reflector's session.

### Reflection item and citation

A `ReflectionItem` is a structured finding about a particular resource target,
with a helpful, neutral, or harmful rating, a reason, evidence citations, and
limitations. A **citation** identifies an event supporting the finding. The
reflector supplies `event_id`; the host resolves its session for saved records.

### Reflection prompt

The instructions that define what the reflector should inspect and how it should
respond. The prompt establishes the reflection task; the evidence supplies the
observations on which the response should be based.

### Native handoff reflection and reflector session

Native handoff reflection continues a fork of a session produced by another agent:

```text
S1  = (p0, t0, ..., pk, tk)
S1R = AR(fork(S1) + RP)
    = S1 + (RP, tR)
Reflection = final response within tR
```

`S1` is the original session. `S1R` is the **reflector session**, including inherited
history, the appended prompt, and the reflector trajectory `tR`. The inherited session
is the evidence, so no separate evidence payload is attached. The original session
remains unchanged. The equation describes logical history; a runtime may store
inherited history by reference.

For a selected trajectory, fork the session prefix through its recorded native
turn. Earlier prompts and trajectories remain context; later turns are excluded.
A native turn ID must be available and unambiguous.

`NativeSessionRef` locates the source conversation and an optional turn cutoff. `ForkReceipt` records the
relationship between source and child. `HandoffReflection` is the saved result
envelope containing the reflection and the session locators. The bundled native
handoff adapter currently supports Codex.

### Offline handoff plan

A `HandoffPlan` packages a task, evidence, and observed workspace identity for
later use. Preparing a plan is an offline operation. Native handoff reflection
uses a native session reference and prompt through a separate API.

### Session translation

Conversion of text messages between provider formats or into Markdown.
`SessionTranslator` creates a `ConversionPlan` with an explicit target format.
Translation preserves a selected representation of messages; it does not provide
the complete event history and continuity of a native fork.

Source: [reflection engine](thearc/learning/reflection/engine.py),
[native handoff](thearc/learning/reflection/handoff.py),
[offline plans](thearc/learning/runtime/handoff.py),
[handoff contract](HANDOFF.md).

## Curation and evaluation

### Curation

The process of using reflections and change history to propose and apply justified
changes to a MetaAgent. A **curator** performs this work. Its result can include
additions, edits, removals, or a decision to keep the configuration unchanged.

The Python `Curation` model represents **one proposed operation** on a resource or
section, including its rationale and links to supporting reflections and evidence.
`UpdateResult` records the resulting MetaAgent and which operations were applied
or rejected.

### Baseline and candidate

The **baseline** is the starting MetaAgent configuration. A **candidate** is a
proposed adapted configuration. These roles are relative to a particular
adaptation or comparison; producing a candidate does not establish improvement.

### Assessment and ranks

An `Assessment` records a reflection rating against a particular revision of a
resource, with supporting evidence. The `AssessmentLedger` tracks these records
and whether each contributes to counts, including handling overlapping evidence.

`Ranks` are counts of helpful, neutral, and harmful feedback. They summarize
assessments; measured task performance is recorded separately by evaluation.

### Curation epoch

A committed curation record linking the resulting configuration, reflections,
changes, assessments, and parent epoch. Epoch history provides the ancestry of
configuration changes. Committing an epoch does not install the configuration or
automatically assign a new release version.

### Evaluation

Measurement of agent behavior against defined tasks and criteria. The current
`compare_agents()` API compares baseline and candidate MetaAgents on the same
held-out tasks through a caller-supplied evaluator.

`TaskScore` records success, cost, and latency. `EvaluationResult` holds individual
scores and aggregate comparisons. Reflection can suggest why behavior occurred;
evaluation measures performance under the chosen test conditions.

### ACE and adaptation run

**ACE** means agentic context engineering: using recorded experience to improve
an agent's configuration. An adaptation run coordinates selection, evidence
preparation, reflection, curation, and optionally evaluation. `AcePipeline` and
the flow helpers compose these steps.

A **journal** records lifecycle events. A **checkpoint** saves state at a pipeline
stage. These records support inspection and recovery mechanisms implemented by
the caller or workflow.

### Workspace and installation

A **MetaAgent workspace** is the canonical filesystem representation saved by
`MetaAgent.to_workspace()`. An **execution workspace** is the project or worktree
where an agent performs tasks. Specify which meaning applies when using the word.

**Installation** writes a MetaAgent's resources into a target harness's
configuration layout. It is a separate action from generating a reflection,
committing a curation epoch, or evaluating a candidate.

Source: [learning contracts](thearc/learning/ace/pipeline.py),
[assessments](thearc/learning/curation/assessments.py),
[epochs](thearc/learning/curation/epochs.py),
[evaluation](thearc/learning/ace/evaluation.py).
