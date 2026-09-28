"""Freeze, export, and inspect immutable session evidence."""

import json
from dataclasses import asdict
from pathlib import Path

import click

from thearc.learning.evidence.exports import render_context, write_files
from thearc.learning.evidence.snapshot import EvidenceSnapshot, check_destination, write_json_exclusive
from thearc.learning.evidence.tools import session_tools
from thearc.learning.sessions.store import SessionStore


@click.group(name="evidence")
@click.option("--index", type=click.Path(path_type=Path), default=Path("~/.thearc/learning.sqlite"), show_default=True)
@click.pass_context
def evidence(ctx, index):
    """Freeze, export, and inspect evidence without invoking an agent."""
    ctx.obj = index


@evidence.command("snapshot")
@click.option("--session-id", multiple=True, required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--max-bytes", type=click.IntRange(min=1), default=100_000_000)
@click.pass_obj
def snapshot_command(index, session_id, output, max_bytes):
    """Freeze redacted, rank-free indexed evidence without invoking an agent."""
    try:
        if not Path(index).expanduser().is_file():
            raise ValueError("An existing session index is required")
        with SessionStore(index) as store:
            evidence = store.snapshot(session_ids=list(session_id), max_bytes=max_bytes)
        evidence.save(output)
        click.echo(json.dumps(evidence.manifest, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@evidence.command("export")
@click.option("--snapshot", "snapshot_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--format", "output_format", type=click.Choice(["jsonl", "markdown", "context"]), default="jsonl")
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--max-chars", type=click.IntRange(min=1), default=20_000)
def export_command(snapshot_path, output_format, output, max_chars):
    """Export a saved snapshot; never installs evidence as agent instructions."""
    try:
        evidence = EvidenceSnapshot.load(snapshot_path)
        if output_format == "context":
            context = render_context(evidence, max_chars=max_chars)
            output = check_destination(output, tuple(evidence.manifest["blocked_root_hashes"]))
            output.mkdir(parents=True, exist_ok=False)
            write_json_exclusive(output / "context.json", asdict(context))
        else:
            write_files(evidence, output, format=output_format)
        click.echo(str(output))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@evidence.command("inspect")
@click.option("--snapshot", "snapshot_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--tool", default="list_sessions")
@click.option("--arguments", default="{}", help="JSON tool arguments")
def inspect_command(snapshot_path, tool, arguments):
    """Preview a scoped evidence-tool response offline, without a model call."""
    try:
        tools = session_tools(EvidenceSnapshot.load(snapshot_path))
        click.echo(json.dumps(tools.call(tool, json.loads(arguments)), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
