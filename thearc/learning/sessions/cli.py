"""Index and query native agent sessions."""

from pathlib import Path

import click

from thearc.learning.sessions.models import SearchFilters, SearchQuery, SourceConfig
from thearc.learning.sessions.store import SessionStore

HARNESSES = ["pi", "codex", "claude", "antigravity", "opencode"]


@click.group(name="sessions")
@click.option("--index", type=click.Path(path_type=Path), default=Path("~/.thearc/learning.sqlite"), show_default=True)
@click.pass_context
def sessions(ctx, index):
    """Index and search local agent transcripts."""
    ctx.obj = index


@sessions.command("index")
@click.option("--source", required=True, type=click.Choice(HARNESSES))
@click.option("--format", "source_format", type=click.Choice(["native", "dataclaw"]), default="native")
@click.option("--root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--source-id", help="Stable identity for this source; defaults to harness and absolute root.")
@click.pass_obj
def index_command(index, source, root, source_id, source_format):
    """Register a source and synchronize all registered sources."""
    with SessionStore(index) as service:
        service.register_source(
            SourceConfig(
                id=source_id or f"{source}:{source_format}:{root.resolve()}",
                harness=source,
                root=root,
                format=source_format,
            )
        )
        click.echo(service.sync().model_dump_json(indent=2))


@sessions.command("sync")
@click.pass_obj
def sync_command(index):
    """Refresh all previously registered sources."""
    with SessionStore(index) as service:
        click.echo(service.sync().model_dump_json(indent=2))


@sessions.command("search")
@click.argument("text")
@click.option("--mode", type=click.Choice(["literal", "lexical"]), default="literal")
@click.option("--harness", multiple=True, type=click.Choice(HARNESSES))
@click.option("--kind", multiple=True)
@click.option("--action", multiple=True)
@click.option("--ignore-case", is_flag=True)
@click.option("--limit", type=click.IntRange(1, 1000), default=20)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.pass_obj
def search_command(index, text, mode, harness, kind, action, ignore_case, limit, offset):
    """Return matching evidence as JSON."""
    query = SearchQuery(
        text=text,
        mode=mode,
        case_sensitive=not ignore_case,
        limit=limit,
        offset=offset,
        filters=SearchFilters(harnesses=list(harness), kinds=list(kind), action_kinds=list(action)),
    )
    with SessionStore(index) as service:
        click.echo(service.search(query).model_dump_json(indent=2))


@sessions.command("show")
@click.argument("event_id")
@click.option("--context", type=click.IntRange(0, 1000), default=3)
@click.pass_obj
def show_command(index, event_id, context):
    """Show an event with context from its run or Pi ancestry."""
    with SessionStore(index) as service:
        try:
            events = service.get_context(event_id, context, context)
        except KeyError as exc:
            raise click.ClickException(f"Unknown event: {event_id}") from exc
        for event in events:
            click.echo(event.model_dump_json())


@sessions.command("trace")
@click.argument("run_id")
@click.pass_obj
def trace_command(index, run_id):
    """Show recorded child-run associations and delegation calls."""
    with SessionStore(index) as service:
        try:
            click.echo(service.get_delegation_trace(run_id).model_dump_json(indent=2))
        except KeyError as exc:
            raise click.ClickException(f"Unknown run: {run_id}") from exc


@sessions.command("add")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--harness", type=click.Choice(HARNESSES), help="Skip detection and use this harness.")
@click.option("--format", "source_format", type=click.Choice(["native", "dataclaw"]), default="native")
@click.pass_obj
def add_command(index, paths, harness, source_format):
    """Register transcript directories or collector batches (harness detected) and sync them."""
    from thearc.resources import Sessions

    with Sessions.open(index) as store:
        try:
            for path in paths:
                source = store.add(path, harness=harness, format=source_format)
                click.echo(f"{source.harness:<12} {source.root}")
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc


@sessions.command("sources")
@click.pass_obj
def sources_command(index):
    """Show registered source directories."""
    with SessionStore(index) as store:
        for source in store.list_sources():
            click.echo(f"{source.harness:<12} {source.format:<9} {source.root}")


@sessions.command("list")
@click.option("--source-id", multiple=True)
@click.option("--harness", multiple=True, type=click.Choice(HARNESSES))
@click.option("--since", help="ISO date/time; sessions started at or after it.")
@click.option("--until", help="ISO date/time; sessions started before it.")
@click.option("--limit", type=click.IntRange(1, 1000), default=100)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.option("--json", "as_json", is_flag=True, help="One JSON record per line.")
@click.pass_obj
def list_command(index, source_id, harness, since, until, limit, offset, as_json):
    """List indexed sessions, newest first, without syncing sources."""
    from thearc.resources import Sessions

    try:
        with Sessions.open(index) as store:
            records = store.list(harness=harness, source=source_id, since=since, until=until)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    for record in records[offset : offset + limit]:
        if as_json:
            click.echo(record.model_dump_json())
        else:
            title = (record.title or record.cwd or "").replace("\n", " ")
            click.echo(
                f"{record.id[:12]}  {record.harness:<11} {(record.started_at or '')[:19]:<19}  "
                f"{record.event_count:>5} events  {title[:60]}"
            )


@sessions.command("render")
@click.argument("session", required=False)
@click.option("--trajectory", type=click.IntRange(min=0), help="Zero-based trajectory index.")
@click.option("--through", is_flag=True, help="Include the prefix through the selected trajectory.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "thearc", "codex", "claude"]),
    default="markdown",
    show_default=True,
)
@click.option("--header/--no-header", default=True, help="Markdown: prefix a session summary.")
@click.pass_obj
def render_command(index, session, trajectory, through, output_format, header):
    """Render SESSION (any ID, native ID, run ID or unique prefix; default: latest)."""
    from thearc.resources import Sessions

    try:
        if through and trajectory is None:
            raise ValueError("--through requires --trajectory")
        with Sessions.open(index) as store:
            selected = store.get(session)
        meta = selected.metadata
        trajectories = selected.split_into_trajectories()
        scope = "whole session"
        if trajectory is not None:
            scope = f"trajectory {trajectory} of {len(trajectories)}" + (" with prefix" if through else "")
            chosen = trajectories[trajectory]
            selected = selected.through(chosen) if through else chosen
        if output_format != "markdown":
            click.echo(selected.to_jsonl(output_format), nl=False)
            return
        if header:
            click.echo(
                f"# Session {meta.native_id}\n\n"
                f"- Harness: {meta.harness}\n- Indexed ID: {meta.id}\n"
                f"- Started: {meta.started_at or 'unknown'}\n- Directory: {meta.cwd or 'unknown'}\n"
                f"- Showing: {scope} ({len(trajectories)} trajectories in session)\n"
            )
        click.echo(selected.to_markdown(), nl=False)
    except (ValueError, KeyError, IndexError, LookupError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
