"""Run curation from saved reflections and inspect committed epochs."""

import json
from pathlib import Path

import click

from thearc.learning.curation.epochs import epoch_history, load_epoch
from thearc.learning.reflection.cli import IMPLEMENTATIONS, load_agent, runner_for
from thearc.learning.reflection.runtime import ReflectionError
from thearc.models import MetaAgent
from thearc.resources import Curator, Reflection


@click.group()
def curation():
    """Curate configuration and inspect committed learning epochs."""


def _summary(epoch):
    return {
        "epoch_id": epoch["epoch_id"],
        "parent": epoch["parent"],
        "created_at": epoch["created_at"],
        "name": epoch["agent"]["name"],
        "version": epoch["agent"].get("version"),
        "summary": epoch["summary"],
        "changes": [
            {key: change[key] for key in ("id", "target", "operation", "rationale", "reflection_ids")}
            for change in epoch["changes"]
        ],
    }


@curation.command("run")
@click.option("--agent", "agent_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--reflections", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="Epoch history root.")
@click.option("--model", required=True)
@click.option("--implementation", type=click.Choice(IMPLEMENTATIONS), required=True)
@click.option("--runner", help="Custom implementation: trusted package.module:function.")
@click.option("--result-version")
@click.option("--timeout", type=click.FloatRange(min=1), default=300)
def run_command(agent_path, reflections, prompt_file, output, model, implementation, runner, result_version, timeout):
    """Curate saved Reflection artifacts and commit a validated epoch. Uses model quota."""
    try:
        if any(
            source.resolve() == output.resolve() or (source.is_dir() and source.resolve() in output.resolve().parents)
            for source in (agent_path, reflections, prompt_file)
        ):
            raise ValueError("Output must be outside input paths")
        paths = sorted(reflections.rglob("reflection.json")) if reflections.is_dir() else [reflections]
        if not paths:
            raise ValueError("No reflection.json artifacts found")
        findings = [Reflection.load(path) for path in paths]
        curator = Curator(
            implementation=implementation,
            model=model,
            timeout_seconds=timeout,
            runner=runner_for(implementation, runner),
        )
        result = curator.curate(
            prompt_file.read_text(),
            findings,
            agent=load_agent(agent_path),
            output=output,
            result_version=result_version,
        )
        from thearc.resources.immutable import thaw

        click.echo(json.dumps(_summary(thaw(result.epoch)), indent=2))
    except (ValueError, TypeError, OSError, KeyError, ImportError, AttributeError, ReflectionError) as exc:
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
        diff = before.diff(
            after, include_ranks=include_ranks, include_version=not no_version, context_lines=context_lines
        )
        click.echo(diff, nl=False) if diff else click.echo("No differences with selected options.")
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
