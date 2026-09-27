# thearc ⚡

`thearc` is an extensible meta-framework for collecting and exploring episodic sessions from AI agents. Rather than prescribing one agent design, it provides the abstractions needed to turn agent traces into evidence for evaluating and improving an agentic system’s context files, skills, hooks, prompts, and MCP integrations.

Build agentic context-engineering pipelines on top of `thearc`: inspect sessions, extract reusable lessons and failures, curate versioned updates, evaluate their effect, and install the resulting capabilities into agent environments. The framework is designed to let these pipelines evolve as new artifacts and evaluation methods emerge, drawing direction from work on [agentic context engineering](https://arxiv.org/pdf/2510.04618), [meta-skill evolution](https://arxiv.org/pdf/2607.05297), [self-evolving agent skills](https://arxiv.org/pdf/2608.02636), [self-evolving agents](https://arxiv.org/html/2507.21046v4), and [meta context engineering](https://arxiv.org/pdf/2601.21557).

## Code layout and experiment collection

The FastAPI test fixture is a Git submodule at `tests/fastapi`, pointing to
`https://github.com/micmurawski/fastapi.git` and pinned to a specific commit.
Initialize it after cloning:

```sh
git submodule update --init --recursive
```

Alternatively, clone thearc with `git clone --recurse-submodules ...`.
Graphify integration tests require this fixture; pytest and Ruff exclude the
submodule's own code/tests from automatic discovery. Experiment edits inside
it belong to the FastAPI repository, not thearc. Commit/push them there before
updating the parent repository's submodule pointer.

`thearc.learning` contains session indexing, reflections, evaluation, and ACE.
It replaces the misleading `thearc.history` Python package; update imports to
`thearc.learning` (public class names and SQLite schema are unchanged).
The CLI uses `thearc sessions`; `thearc history` remains a compatibility alias.

Use `scripts/run_codex_prompts.sh` to collect verified native Codex sessions.
Each prompt runs in a separate retained Git worktree at the same pinned commit,
leaving the source FastAPI checkout's files, index, and branch unchanged.
See the [prompt collection guide](prompts/README.md) for batch execution,
failure handling, artifacts, and indexing. Existing experimental logs are preserved.
See the [collection review](docs/experiment-collection-review.md) for the failure
diagnosis and remaining experiment-quality limitations.

## Codex-backed ACE reflections

`CodexReflector` prompts a Codex SDK agent to inspect normalized sessions against a rank-free `MetaAgent`. It returns `Reflection.items`: evidence-backed helpful/neutral/harmful ratings of specific skills, sections, hooks, or other configuration parts, each with a reason. Historical ranks are hidden to avoid bias. Original sessions can come from Codex, Claude, Antigravity, or Pi. Reflection-only runs neither update stored ranks nor curate/change the configuration.

Install the optional integration with `pip install -e '.[codex]'`. Start with the [Graphify reflection guide](docs/codex-reflections.md) to preview input before running inference. Use `scripts/generate_reflections.py` for the entire experimental session corpus: reflection-only batches, coverage reporting, and resumable artifacts, without curation or configuration changes. The implementation and offline/runtime-startup tests are available; live model quality validation is still pending.
