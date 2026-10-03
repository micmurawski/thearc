"""Provider-neutral reflection generation and saved finding inspection."""

import json
from pathlib import Path
from typing import get_args

import click

from thearc.learning.ace.pipeline import Reflection
from thearc.learning.reflection.engine import ReflectionError, ReflectorConfig
from thearc.learning.reflection.factory import BUILTIN_BACKENDS, create_reflector
from thearc.learning.reflection.flow import run_reflections
from thearc.learning.reflection.handoff import (
    HANDOFF_REFLECTION_PROMPT,
    HandoffReflection,
    HandoffReflectorConfig,
    NativeSessionRef,
    create_handoff_reflector,
)
from thearc.learning.sessions.models import Harness
from thearc.learning.sessions.store import SessionStore
from thearc.models import MetaAgent


@click.group()
def reflection():
    """Generate and inspect evidence-backed configuration assessments."""


@reflection.command("run")
@click.option("--agent", "agent_path", type=click.Path(exists=True, path_type=Path), required=True,
              help="MetaAgent JSON or canonical workspace; no project or provider is assumed.")
@click.option("--index", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True,
              help="Existing session index; native logs may come from any supported agent.")
@click.option("--source-id", multiple=True, required=True,
              help="Indexed source to assess; repeat for multiple sources.")
@click.option("--backend", type=click.Choice([*BUILTIN_BACKENDS, "custom"]), required=True,
              help="Explicit reflection runtime; independent of source-session format. No default.")
@click.option("--model", required=True, help="Model identifier understood by the selected runtime.")
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--batch-size", type=click.IntRange(min=1), default=3, show_default=True)
@click.option("--evidence-view", type=click.Choice(["compact", "detailed"]), default="compact", show_default=True,
              help="Evidence supplied to the agent; both views omit source harness metadata.")
@click.option("--timeout", type=click.FloatRange(min=1), default=300, show_default=True)
@click.option("--execute", is_flag=True,
              help="Invoke the selected runtime (may consume quota); otherwise preview only.")
@click.option("--resume", is_flag=True, help="Reuse matching artifacts in an existing output directory.")
@click.option("--runner", help="Custom backend only: trusted Python callable, package.module:function.")
@click.option("--runtime", type=click.Choice(list(get_args(Harness))), help="Custom backend's agent identity.")
@click.option("--adapter-version", help="Custom backend's code/runtime version for cache identity.")
def run_command(agent_path, index, source_id, backend, model, output, batch_size, timeout,
                execute, resume, runner, runtime, adapter_version, evidence_view):
    """Preview or generate batched reflections; never curate or install configuration.

    Custom adapters are trusted application code imported only with --execute.
    Review generated artifacts before sharing; redaction is best effort.
    """
    try:
        destination = output.resolve()
        if any(part.is_symlink() for part in (output.absolute(), *output.absolute().parents)):
            raise ValueError("Output must not contain symlinks")
        if destination == agent_path.resolve() or destination == index.resolve() or (
            agent_path.is_dir() and agent_path.resolve() in destination.parents
        ):
            raise ValueError("Output must be outside the input configuration and index")
        agent = (MetaAgent.from_workspace(agent_path) if agent_path.is_dir()
                 else MetaAgent.model_validate_json(agent_path.read_text(encoding="utf-8")))
        reflector = create_reflector(
            backend, ReflectorConfig(model=model, timeout_seconds=timeout, evidence_view=evidence_view),
            runner=runner, runtime=runtime, adapter_version=adapter_version,
        )
        with SessionStore(index) as store:
            result = run_reflections(store, agent, reflector, output, source_ids=list(source_id),
                                     batch_size=batch_size, execute=execute, resume=resume)
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc
    if result["failed_batches"] or result["status"] == "no_evidence":
        raise click.ClickException("Reflection run incomplete; inspect the saved batch report")


@reflection.command("handoff")
@click.option("--backend", required=True, type=click.Choice(["codex"]),
              help="Native session runtime. Uses a persisted fork, with no history conversion.")
@click.option("--session-id", required=True, help="Native source session ID, not the indexed canonical ID.")
@click.option("--model", required=True, help="Reflector model; may differ from the model that produced the source.")
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Reflection prompt to append verbatim; otherwise use the default reflection prompt.")
@click.option("--effort", default="medium", show_default=True)
@click.option("--timeout", type=click.FloatRange(min=1), default=180, show_default=True)
@click.option("--output", required=True, type=click.Path(path_type=Path))
@click.option("--execute", is_flag=True, help="Fork and run reflection; otherwise save the prompt preview only.")
def handoff_command(backend, session_id, model, prompt_file, effort, timeout, output, execute):
    """Continue a native fork with only a reflection prompt: AR(fork(S1) + RP) = S1R."""
    try:
        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file else HANDOFF_REFLECTION_PROMPT
        source = NativeSessionRef(runtime=backend, session_id=session_id)
        reflector = create_handoff_reflector(
            backend, HandoffReflectorConfig(model=model, reasoning_effort=effort, timeout_seconds=timeout),
            artifact_dir=output,
        )
        prepared = reflector.prepare(source, prompt=prompt)
        if any(part.is_symlink() for part in (output.absolute(), *output.absolute().parents)):
            raise ValueError("Output must not contain symlinks")
        output.mkdir(parents=True, exist_ok=False)
        (output / "preview.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        if execute:
            result = reflector.reflect(source, prompt=prompt)
            click.echo(result.model_dump_json(indent=2))
        else:
            click.echo(json.dumps({"status": "prepared", "preview": str(output / "preview.json")}, indent=2))
    except (ValueError, OSError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc


@reflection.command("show")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--reflection-id", help="Select one saved finding by ID within a directory.")
def show_command(path, reflection_id):
    """Read saved ratings, reasons, limitations and session/event citations; no inference.

    PATH is a reflection JSON file, artifact directory, or batch output directory.
    """
    try:
        paths = sorted([*path.rglob("reflection.json"), *path.rglob("handoff.json")]) if path.is_dir() else [path]
        findings = [(HandoffReflection if item.name == "handoff.json" else Reflection).model_validate_json(
            item.read_text(encoding="utf-8")) for item in paths]
        if reflection_id:
            findings = [finding for finding in findings if finding.id == reflection_id]
        if not findings:
            raise ValueError("No matching saved reflections found (previews do not contain findings)")
        click.echo(json.dumps({"reflections": [finding.model_dump(mode="json", exclude={"raw"})
                                               for finding in findings]}, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
