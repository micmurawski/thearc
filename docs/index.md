# thearc

`thearc` helps you improve an agent's configuration using evidence from its past
work. A **MetaAgent** holds skills, reference files, instructions, hooks, and MCP
configuration independently of the agent harness that installs them.

An ACE run has two learning steps:

1. **Reflect:** inspect recorded sessions and explain which configuration parts
   were helpful, neutral, or harmful.
2. **Curate:** use those findings and change history to produce an adapted MetaAgent.

You can review the resulting diff and install the configuration into another
harness. A change is not proof of improvement: evaluation and release decisions
remain your responsibility.

## Start here

[Install the project](start/installation.md), then follow
[your first ACE run](start/first-ace.md). It starts with native session logs and
ends with an adapted configuration—using ordinary Python calls, without a tracker.

## Find the task you need

| Task | Guide |
| --- | --- |
| Import, save, or install configuration | [MetaAgent](guides/metaagent.md) |
| Index logs and select sessions by date | [Sessions](guides/sessions.md) |
| Save portable evidence or prepare an offline handoff | [Evidence](guides/evidence.md) |
| Inspect configuration without changing it | [Reflections](guides/reflections.md) |
| Apply findings and inspect reasons behind edits | [Curation](guides/curation.md) |
| Choose and configure a runtime | [Runtime support](reference/runtimes.md) |

The **source harness**, **reflection/curation runtime**, and **installation target**
are separate choices. You do not need to convert or resume a native conversation
to reflect on it using another runtime.

These pages describe current behavior. Design proposals and experiment reports
are kept outside the user documentation; see [development](development/index.md).
