"""Index and query native agent sessions."""

from pathlib import Path

import click

from thearc.learning.sessions.models import SearchFilters, SearchQuery, SourceConfig
from thearc.learning.sessions.store import SessionStore


@click.group(name="sessions")
@click.option("--index", type=click.Path(path_type=Path), default=Path("~/.thearc/learning.sqlite"), show_default=True)
@click.pass_context
def sessions(ctx, index):
    """Index and search local agent transcripts."""
    ctx.obj = index


@sessions.command("index")
@click.option("--source", required=True, type=click.Choice(["pi", "codex", "claude", "antigravity"]))
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
@click.option("--harness", multiple=True, type=click.Choice(["pi", "codex", "claude", "antigravity"]))
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


@sessions.command("list")
@click.option("--source-id", multiple=True)
@click.option("--limit", type=click.IntRange(1, 1000), default=100)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.pass_obj
def list_command(index, source_id, limit, offset):
    """List canonical session identities without syncing sources."""
    with SessionStore(index) as store:
        for session in store.list_sessions(SearchFilters(source_ids=list(source_id)), limit=limit, offset=offset):
            click.echo(session.model_dump_json())
