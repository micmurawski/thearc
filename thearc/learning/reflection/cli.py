"""Provider-neutral reflection generation and saved finding inspection."""

import json
from pathlib import Path
from typing import get_args

import click

from thearc.learning.ace.pipeline import Reflection
from thearc.learning.reflection.engine import ReflectionError, ReflectorConfig
from thearc.learning.reflection.factory import BUILTIN_BACKENDS, create_reflector
from thearc.learning.reflection.flow import run_reflections
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
@click.option("--timeout", type=click.FloatRange(min=1), default=300, show_default=True)
@click.option("--execute", is_flag=True,
              help="Invoke the selected runtime (may consume quota); otherwise preview only.")
@click.option("--resume", is_flag=True, help="Reuse matching artifacts in an existing output directory.")
@click.option("--runner", help="Custom backend only: trusted Python callable, package.module:function.")
@click.option("--runtime", type=click.Choice(list(get_args(Harness))), help="Custom backend's agent identity.")
@click.option("--adapter-version", help="Custom backend's code/runtime version for cache identity.")
def run_command(agent_path, index, source_id, backend, model, output, batch_size, timeout,
                execute, resume, runner, runtime, adapter_version):
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
            backend, ReflectorConfig(model=model, timeout_seconds=timeout),
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


@reflection.command("show")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--reflection-id", help="Select one saved finding by ID within a directory.")
def show_command(path, reflection_id):
    """Read saved ratings, reasons, limitations and session/event citations; no inference.

    PATH is a reflection JSON file, artifact directory, or batch output directory.
    """
    try:
        paths = sorted(path.rglob("reflection.json")) if path.is_dir() else [path]
        findings = [Reflection.model_validate_json(item.read_text(encoding="utf-8")) for item in paths]
        if reflection_id:
            findings = [finding for finding in findings if finding.id == reflection_id]
        if not findings:
            raise ValueError("No matching saved reflections found (previews do not contain findings)")
        click.echo(json.dumps({"reflections": [finding.model_dump(mode="json", exclude={"raw"})
                                               for finding in findings]}, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
