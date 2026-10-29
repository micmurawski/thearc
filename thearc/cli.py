"""The ``thearc`` command line."""

from pathlib import Path

import click

from thearc.evolve.cli import evolve, skills
from thearc.sessions.cli import sessions
from thearc.utils import AliasedGroup


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=AliasedGroup)
@click.version_option(package_name="thearc")
def main() -> None:
    """thearc: collect agent sessions, evolve agent configurations, and see how they evolved."""


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


HARNESS_NAMES = ("claude", "codex", "pi", "opencode", "antigravity")


@main.group(cls=AliasedGroup)
def agent() -> None:
    """Install a MetaAgent from its workspace repository into a harness, or remove it."""


def _target(harness: str, project: Path | None, global_: bool):  # type: ignore[no-untyped-def]
    """The folder to install into and the harness's layout there: a project's, or the harness's own (--global)."""
    from thearc.resources.harness import get_harness, global_layout

    if global_ and project is not None:
        raise click.UsageError("choose --project or --global, not both")
    if global_:
        return global_layout(harness)  # type: ignore[arg-type]
    return (project or Path(".")).resolve(), get_harness(harness)  # type: ignore[arg-type]


@agent.command("install")
@click.option("--repo", "--url", "--path", "source", required=True, metavar="URL|PATH",
              help="The agent's workspace repository: a git URL or a local folder.")
@click.option("--version", "version", metavar="VERSION",
              help="The release to install, e.g. 1.0.0 (its release/<version> branch). Default: main.")
@click.option("--harness", type=click.Choice(HARNESS_NAMES), required=True, help="The harness to install for.")
@click.option("--project", type=click.Path(file_okay=False, path_type=Path),
              help="Install into this project (the default, with the current folder).")
@click.option("--global", "global_", is_flag=True,
              help="Install for every project: into the harness's own folder (~/.claude, ~/.codex, ...).")
@click.option("--force", is_flag=True, help="Overwrite files the agent's earlier installs did not write.")
def install_agent(source: str, version: str | None, harness: str, project: Path | None, global_: bool,
                  force: bool) -> None:
    """Install the MetaAgent released in a workspace repository into HARNESS.

    \b
    thearc agent install --repo https://github.com/me/graphify --version 1.0.2 --harness claude
    thearc agent install --path ../graphify --harness codex --global
    """
    from thearc.resources.git import GitError
    from thearc.resources.harness import InstallConflict, install
    from thearc.resources.source import agent_from

    try:
        root, layout = _target(harness, project, global_)
        configuration = agent_from(source, version)
        result = install(configuration, layout, root, force=force)
    except InstallConflict as error:
        raise click.ClickException(f"{error}; pass --force to overwrite") from None
    except (ValueError, GitError, OSError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(f"Installed {configuration.name} {configuration.version or '(no version)'} for {harness} "
               f"into {root}: {len(result.files)} file(s), {len(result.context)} context document(s), "
               f"{len(result.hooks)} hook(s), {len(result.mcps)} MCP server(s).")
    if result.skipped:
        click.echo(f"Skipped (the harness cannot hold them here): {', '.join(result.skipped)}")


@agent.command("uninstall")
@click.option("--name", required=True, help="The installed agent's name.")
@click.option("--harness", type=click.Choice(HARNESS_NAMES), required=True, help="The harness to remove it from.")
@click.option("--project", type=click.Path(file_okay=False, path_type=Path),
              help="Remove it from this project (the default, with the current folder).")
@click.option("--global", "global_", is_flag=True, help="Remove it from the harness's own folder.")
def uninstall_agent(name: str, harness: str, project: Path | None, global_: bool) -> None:
    """Remove what an install of agent NAME wrote for HARNESS; everything else stays."""
    from thearc.resources.harness import installed_agents, uninstall

    root, layout = _target(harness, project, global_)
    if harness not in installed_agents(root).get(name, {}):
        raise click.ClickException(f"{name} is not installed for {harness} in {root}")
    uninstall(layout, root, name=name)
    click.echo(f"Removed {name} from {harness} in {root}.")


main.add_command(sessions)
main.add_command(skills)
main.add_command(evolve)
