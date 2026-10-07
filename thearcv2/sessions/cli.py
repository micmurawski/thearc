"""``sessions`` commands: register transcript sources, synchronize, find, and render sessions."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import click

from thearcv2.harnesses import HARNESSES

from .sessions import DEFAULT_INDEX, Sessions


def _errors(command):
    """Report expected failures (unknown references, bad input, unreadable files) as CLI errors."""
    @functools.wraps(command)
    def wrapper(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except (ValueError, LookupError, OSError) as exc:
            raise click.ClickException(str(exc).strip("'")) from exc
    return wrapper


def _open(index: Path) -> Sessions:
    return Sessions.open(index)


def _one_line(text: str | None, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


@click.group(name="sessions")
@click.option("--index", type=click.Path(path_type=Path), default=DEFAULT_INDEX, show_default=True,
              help="Session index file.")
@click.pass_context
def sessions(ctx, index):
    """Collect agent transcripts into a session index, then find and render sessions."""
    ctx.obj = index


@sessions.command("add")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--harness", type=click.Choice(HARNESSES), help="Skip detection and use this harness.")
@click.option("--format", "source_format", type=click.Choice(["native", "dataclaw"]), default="native",
              show_default=True)
@click.option("--source-id", help="Stable ID for the source (one path only); defaults to harness, format and root.")
@click.option("--no-sync", is_flag=True, help="Register only; synchronize later with `sync`.")
@click.pass_obj
@_errors
def add_command(index, paths, harness, source_format, source_id, no_sync):
    """Register transcript directories or collector batches and synchronize them."""
    if source_id and len(paths) > 1:
        raise click.UsageError("--source-id applies to a single path")
    with _open(index) as store:
        for path in paths:
            source = store.add(path, harness=harness, format=source_format, source_id=source_id, sync=not no_sync)
            click.echo(f"{source.harness:<12} {source.root}")


@sessions.command("sources")
@click.pass_obj
@_errors
def sources_command(index):
    """Show registered sources."""
    with _open(index) as store:
        for source in store.sources():
            click.echo(f"{source.harness:<12} {source.format:<9} {source.root}  [{source.id}]")


@sessions.command("sync")
@click.option("--source-id", multiple=True, help="Only these sources (default: all).")
@click.pass_obj
@_errors
def sync_command(index, source_id):
    """Read new and changed transcripts from registered sources."""
    with _open(index) as store:
        click.echo(store.sync(source_ids=list(source_id) or None).model_dump_json(indent=2))


@sessions.command("list")
@click.option("--source-id", multiple=True)
@click.option("--harness", multiple=True, type=click.Choice(HARNESSES))
@click.option("--since", help="ISO date/time; sessions started at or after it.")
@click.option("--until", help="ISO date/time; sessions started before it.")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.option("--json", "as_json", is_flag=True, help="One JSON record per line.")
@click.pass_obj
@_errors
def list_command(index, source_id, harness, since, until, limit, offset, as_json):
    """List indexed sessions, newest first, without synchronizing."""
    with _open(index) as store:
        records = store.list(harness=harness, source=source_id, since=since, until=until)
    for record in records[offset:offset + limit]:
        if as_json:
            click.echo(record.model_dump_json())
        else:
            click.echo(f"{record.id[:12]}  {record.harness:<11} {(record.started_at or '')[:19]:<19}  "
                       f"{record.event_count:>5} events  {_one_line(record.title or record.cwd, 60)}")


@sessions.command("trajectories")
@click.argument("session", required=False)
@click.pass_obj
@_errors
def trajectories_command(index, session):
    """List the trajectories of SESSION (any ID, native ID, run ID or unique prefix; default: latest)."""
    with _open(index) as store:
        selected = store.get(session)
        split = selected.trajectories
        click.echo(f"{selected.metadata.native_id}: {len(split)} trajectories, "
                   f"{len(split.unassigned_events)} unassigned events")
        for number, trajectory in enumerate(split):
            click.echo(f"{number:>3}  {trajectory.boundary:<15} {len(trajectory.events):>5} events  "
                       f"{_one_line(trajectory.prompt_text, 70)}")


@sessions.command("search")
@click.argument("text", required=False)
@click.option("--mode", type=click.Choice(["literal", "lexical"]), default="literal", show_default=True)
@click.option("--ignore-case", is_flag=True, help="Literal mode only; lexical search always ignores case.")
@click.option("--harness", multiple=True, type=click.Choice(HARNESSES))
@click.option("--session", "session_id", multiple=True, help="Indexed session ID.")
@click.option("--kind", multiple=True, help="Event kind, e.g. message, tool_call, tool_result.")
@click.option("--role", multiple=True)
@click.option("--tool", "tool_name", multiple=True)
@click.option("--action", "action_kind", multiple=True, help="Normalized action, e.g. shell, file.edit.")
@click.option("--status", multiple=True, help="Tool result status, e.g. success, failure.")
@click.option("--limit", type=click.IntRange(1, 1000), default=20, show_default=True)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.option("--json", "as_json", is_flag=True, help="One JSON hit per line.")
@click.pass_obj
@_errors
def search_command(index, text, mode, ignore_case, harness, session_id, kind, role, tool_name, action_kind, status,
                   limit, offset, as_json):
    """Find events matching TEXT and/or filters, in recorded order."""
    filters = {name: list(values) for name, values in (
        ("harnesses", harness), ("session_ids", session_id), ("kinds", kind), ("roles", role),
        ("tool_names", tool_name), ("action_kinds", action_kind), ("statuses", status)) if values}
    if text is None and not filters:
        raise click.UsageError("give TEXT or at least one filter")
    case_sensitive = False if mode == "lexical" else not ignore_case
    with _open(index) as store:
        hits = store.search(text, mode=mode, case_sensitive=case_sensitive, **filters)
        for hit in hits[offset:offset + limit]:
            event = hit.event
            trajectory = hit.trajectory
            if as_json:
                record = {"session_id": hit.session.id, "native_session_id": hit.session.metadata.native_id,
                          "trajectory_id": trajectory.id if trajectory else None,
                          "match_ranges": hit.match_ranges, "event": event.model_dump()}
                click.echo(json.dumps(record, ensure_ascii=False))
            else:
                label = event.tool_name or event.role or ""
                click.echo(f"{event.id[:12]}  {hit.session.id[:12]}  {event.kind:<11} {label:<10} "
                           f"{_one_line(event.text, 70)}")


@sessions.command("show")
@click.argument("event_id")
@click.option("--context", type=click.IntRange(0, 1000), default=3, show_default=True)
@click.pass_obj
@_errors
def show_command(index, event_id, context):
    """Show an event with surrounding events from its run (JSON lines)."""
    with _open(index) as store:
        try:
            events = store.index.get_context(event_id, context, context)
        except KeyError as exc:
            raise click.ClickException(f"Unknown event: {event_id}") from exc
        for event in events:
            click.echo(event.model_dump_json())


@sessions.command("trace")
@click.argument("run_id")
@click.pass_obj
@_errors
def trace_command(index, run_id):
    """Show a run's recorded sub-agent runs and delegation calls."""
    with _open(index) as store:
        try:
            click.echo(store.index.get_delegation_trace(run_id).model_dump_json(indent=2))
        except KeyError as exc:
            raise click.ClickException(f"Unknown run: {run_id}") from exc


@sessions.command("render")
@click.argument("session", required=False)
@click.option("--trajectory", type=click.IntRange(min=0), help="Zero-based trajectory number (see `trajectories`).")
@click.option("--through", is_flag=True, help="Render the session up to and including the selected trajectory.")
@click.option("--format", "output_format", type=click.Choice(["markdown", "thearc", "codex", "claude"]),
              default="markdown", show_default=True)
@click.option("--header/--no-header", default=True, help="Markdown: prefix a session summary.")
@click.pass_obj
@_errors
def render_command(index, session, trajectory, through, output_format, header):
    """Render SESSION (any ID, native ID, run ID or unique prefix; default: latest)."""
    if through and trajectory is None:
        raise click.UsageError("--through requires --trajectory")
    with _open(index) as store:
        selected = store.get(session)
        meta = selected.metadata
        trajectories = selected.trajectories
        scope = "whole session"
        if trajectory is not None:
            if trajectory >= len(trajectories):
                count = len(trajectories)
                raise click.ClickException(f"Session has {count} trajectories; use 0..{count - 1}")
            scope = f"trajectory {trajectory} of {len(trajectories)}" + (" with prefix" if through else "")
            chosen = trajectories[trajectory]
            selected = selected.through(chosen) if through else chosen
        if output_format != "markdown":
            click.echo(selected.to_jsonl(output_format), nl=False)
            return
        if header:
            click.echo(f"# Session {meta.native_id}\n\n"
                       f"- Harness: {meta.harness}\n- Indexed ID: {meta.id}\n"
                       f"- Started: {meta.started_at or 'unknown'}\n- Directory: {meta.cwd or 'unknown'}\n"
                       f"- Showing: {scope} ({len(trajectories)} trajectories in session)\n")
        click.echo(selected.to_markdown(), nl=False)
