"""The ``thearc`` command line."""

from pathlib import Path

import click

from thearc.sessions.cli import sessions
from thearc.utils import AliasedGroup


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=AliasedGroup)
def main() -> None:
    """thearc: collect agent sessions, and see how agent configurations evolve."""


@main.command()
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), default=Path("report.html"),
              show_default=True, help="HTML file to write.")
@click.option("--ref", "refs", multiple=True, help="Branches to include (default: every local branch).")
@click.option("--reflections", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder of saved reflections (JSON) to show with the ratings they gave.")
def report(workspace: Path, output: Path, refs: tuple[str, ...], reflections: Path | None) -> None:
    """Write an HTML report of how WORKSPACE's agent evolved: ratings and changes per section and revision."""
    from thearc.resources.report import write_report

    path = write_report(workspace, output, refs=list(refs) or None, reflections=reflections)
    click.echo(f"Wrote {path}")


main.add_command(sessions)
