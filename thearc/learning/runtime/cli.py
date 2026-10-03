"""Prepare context plans and run native handoff reflection."""

import json
from pathlib import Path

import click

from thearc.learning.evidence.snapshot import EvidenceSnapshot
from thearc.learning.reflection.cli import handoff_command
from thearc.learning.runtime.handoff import load_handoff, prepare_handoff, save_handoff


@click.group("handoff")
def handoff_commands():
    """Prepare context plans or reflect by continuing a native session fork."""


handoff_commands.add_command(handoff_command, name="reflect")


@handoff_commands.command("prepare")
@click.option("--snapshot", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--task", required=True)
@click.option("--workspace", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--max-chars", type=click.IntRange(min=1), default=20_000)
def handoff_prepare(snapshot, task, workspace, output, max_chars):
    """Save a task and bounded context without invoking an agent or modifying the workspace."""
    try:
        plan = prepare_handoff(EvidenceSnapshot.load(snapshot), task=task, workspace=workspace,
                               max_context_chars=max_chars)
        save_handoff(plan, output)
        click.echo(json.dumps({"sha256": plan.sha256, "plan": plan.data}, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@handoff_commands.command("show")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
def handoff_show(directory):
    """Verify saved hashes and show the exact task/context preview. This is not execution preflight."""
    try:
        plan = load_handoff(directory)
        click.echo(json.dumps({"sha256": plan.sha256, "preview": plan.preview()}, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
