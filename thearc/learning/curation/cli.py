"""Run curation from saved reflections and inspect committed epochs."""

import json
from pathlib import Path
from typing import get_args

import click

from thearc.learning.ace.pipeline import Reflection
from thearc.learning.curation.engine import run_curation
from thearc.learning.curation.epochs import epoch_history, load_epoch
from thearc.learning.curation.factory import BUILTIN_CURATORS, create_curator
from thearc.learning.reflection.engine import ReflectionError, ReflectorConfig
from thearc.learning.sessions.models import Harness
from thearc.models import MetaAgent


@click.group()
def curation():
    """Curate configuration and inspect committed learning epochs."""


def _summary(epoch):
    return {"epoch_id": epoch["epoch_id"], "parent": epoch["parent"], "created_at": epoch["created_at"],
            "name": epoch["agent"]["name"], "version": epoch["agent"].get("version"),
            "summary": epoch["summary"], "changes": [
                {key: change[key] for key in ("id", "target", "operation", "rationale", "reflection_ids")}
                for change in epoch["changes"]
            ]}


@curation.command("run")
@click.option("--agent", "agent_path", type=click.Path(exists=True, path_type=Path), required=True,
              help="Baseline MetaAgent JSON or canonical workspace.")
@click.option("--reflections", type=click.Path(exists=True, path_type=Path), required=True,
              help="One reflection.json or directory containing reflection.json files.")
@click.option("--output", type=click.Path(path_type=Path), required=True, help="Epoch history root.")
@click.option("--model", required=True)
@click.option("--backend", type=click.Choice([*BUILTIN_CURATORS, "custom"]), required=True,
              help="Explicit curation runtime; independent of source-session format. No default.")
@click.option("--runner", help="Custom backend only: trusted Python callable, package.module:function.")
@click.option("--runtime", type=click.Choice(list(get_args(Harness))), help="Custom backend's agent identity.")
@click.option("--adapter-version", help="Custom backend's code/runtime version for provenance.")
@click.option("--result-version")
@click.option("--timeout", type=click.FloatRange(min=1), default=300)
def run_command(agent_path, reflections, output, model, backend, runner, runtime, adapter_version,
                result_version, timeout):
    """Invoke the selected runtime, validate edits, and commit. Uses model quota."""
    try:
        if any(source.resolve() == output.resolve() or source.resolve() in output.resolve().parents
               for source in (agent_path, reflections) if source.is_dir()):
            raise ValueError("Output must be outside input directories")
        agent = (MetaAgent.from_workspace(agent_path) if agent_path.is_dir()
                 else MetaAgent.model_validate_json(agent_path.read_text()))
        paths = sorted(reflections.rglob("reflection.json")) if reflections.is_dir() else [reflections]
        if not paths:
            raise ValueError("No reflection.json artifacts found")
        findings = [Reflection.model_validate_json(path.read_text()) for path in paths]
        curator = create_curator(backend, ReflectorConfig(model=model, timeout_seconds=timeout),
                                 runner=runner, runtime=runtime, adapter_version=adapter_version)
        epoch = run_curation(
            agent, findings, curator,
            output=output, result_version=result_version,
        )
        click.echo(json.dumps(_summary(epoch), indent=2))
    except (ValueError, OSError, KeyError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc


@curation.command("show")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--epoch", "epoch_id", help="Default: current committed HEAD.")
def show_command(directory, epoch_id):
    """Verify and summarize a saved epoch without inference."""
    try:
        epoch = load_epoch(directory, epoch_id)
        if epoch is None:
            raise ValueError("No committed epoch")
        click.echo(json.dumps(_summary(epoch), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@curation.command("history")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
def history_command(directory):
    """Show committed lineage; ignore interrupted/unpublished epochs."""
    try:
        click.echo(json.dumps([_summary(epoch) for epoch in epoch_history(directory)], indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@curation.command("diff")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--epoch", "epoch_id", help="Default: current committed HEAD.")
@click.option("--include-ranks", is_flag=True, help="Include projected rank annotations and counters.")
@click.option("--no-version", is_flag=True, help="Hide MetaAgent release-version changes (not bundled files).")
@click.option("--context-lines", type=click.IntRange(min=0), default=3, show_default=True)
def diff_command(directory, epoch_id, include_ranks, no_version, context_lines):
    """Display baseline-to-result changes for a verified epoch; no inference.

    Output may contain sensitive configuration. Review before sharing.
    """
    try:
        epoch = load_epoch(directory, epoch_id)
        if epoch is None:
            raise ValueError("No committed epoch")
        before = MetaAgent.model_validate(epoch["baseline"])
        after = MetaAgent.model_validate(epoch["agent"])
        diff = before.diff(after, include_ranks=include_ranks,
                           include_version=not no_version, context_lines=context_lines)
        click.echo(diff, nl=False) if diff else click.echo("No differences with selected options.")
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
