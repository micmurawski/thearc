"""``thearc skills install self-evolve``, ``thearc skills uninstall self-evolve``, and ``thearc evolve``."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from thearc.utils import AliasedGroup

from .setup import HARNESSES, RUNTIMES

PROJECT = click.Path(file_okay=False, path_type=Path)


def _errors(command: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            return command(*args, **kwargs)
        except ValueError as error:
            raise click.ClickException(str(error)) from None

    return wrapper


def _json_option(command: Callable[..., None]) -> Callable[..., None]:
    return click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")(command)


# --- skills -------------------------------------------------------------------------------------------------------


@click.group(name="skills", cls=AliasedGroup)
def skills() -> None:
    """thearc's own skills: install them into a project, or remove them."""


@skills.group(name="install", cls=AliasedGroup)
def install() -> None:
    """Install one of thearc's skills into a project."""


@install.command("self-evolve")
@click.option("--project", type=PROJECT, default=Path("."), show_default=True,
              help="The project to evolve (any folder in its git repository).")
@click.option("--harness", required=True, type=click.Choice(HARNESSES),
              help="The harness whose configuration is evolved, from its own sessions.")
@click.option("--runtime", type=click.Choice(RUNTIMES),
              help="The harness that reflects and curates (default: --harness).")
@click.option("--ignore", "ignore", multiple=True, help="Leave a target out of evolution, e.g. skill:vendor-*.")
@click.option("--remote", help="The workspace repository's URL (e.g. git@github.com:org/project-thearc.git): "
              "pushed to now, and recorded in the submodule.")
@_errors
def install_evolve_skill_command(project: Path, harness: str, runtime: str | None, ignore: tuple[str, ...],
                                 remote: str | None) -> None:
    """Set PROJECT up for /evolve in HARNESS: adopt its configuration for that harness as a MetaAgent in a workspace
    repository, added to the project as the git submodule .thearc/workspace, and install the evolve skill."""
    from .setup import WORKSPACE, install_evolve_skill

    setup = install_evolve_skill(project, harness, runtime=runtime, ignore=list(ignore),  # type: ignore[arg-type]
                                 remote=remote)
    agent = setup.agent
    click.echo(f"{agent.name} {agent.version} is set up for evolution in {setup.harness} "
               f"(runtime: {setup.runtime}; workspace: {WORKSPACE}).")
    click.echo(f"  skills: {', '.join(sorted(agent.skills)) or '-'}")
    click.echo(f"  context: {', '.join(sorted(agent.context)) or '-'}")
    click.echo(f"  hooks: {', '.join(sorted(agent.hooks)) or '-'}")
    click.echo(f"  ignored: {', '.join(agent.ignore)}")
    click.echo("Commit the installed files, .gitmodules, .thearc/workspace and .gitignore"
               + ("." if remote else "; give the workspace a remote with `thearc evolve remote URL` to share it.")
               + " Then type /evolve in your agent.")


@skills.group(name="uninstall", cls=AliasedGroup)
def uninstall() -> None:
    """Remove one of thearc's skills from a project."""


@uninstall.command("self-evolve")
@click.option("--project", type=PROJECT, default=Path("."), show_default=True)
@_errors
def uninstall_evolve_skill_command(project: Path) -> None:
    """Remove the evolve skill; the evolved configuration and its history stay."""
    from .setup import uninstall_evolve_skill

    click.echo(f"Removed the evolve skill for {uninstall_evolve_skill(project)}.")


# --- evolve -------------------------------------------------------------------------------------------------------


@click.group(name="evolve", cls=AliasedGroup, invoke_without_command=True)
@click.option("--project", type=PROJECT, default=Path("."), show_default=True,
              help="The project (any folder in its git repository).")
@_json_option
@click.option("--recipe", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Run this recipe instead of the installed evolve skill's evolve.py.")
@click.option("--open", "open_page", is_flag=True, help="Open the round's live progress page in the browser.")
@click.pass_context
@_errors
def evolve(ctx: click.Context, project: Path, as_json: bool, recipe: Path | None, open_page: bool) -> None:
    """Run a round: propose a new version of the project's configuration from its harness's recent sessions. Its
    progress is shown live at .thearc/cache/progress.html."""
    ctx.obj = {"project": project}
    if ctx.invoked_subcommand is not None:
        return
    import webbrowser

    from .round import progress_page, run, write_waiting_page
    from .setup import project_harness

    project_harness(project)  # Set up for evolution, or say how.
    page = progress_page(project)
    click.echo(f"Progress: {page.as_uri()}", err=True)
    if open_page:
        write_waiting_page(page, "Preparing the round…")
        webbrowser.open(page.as_uri())
    proposal = run(project, recipe=recipe)
    if as_json:
        click.echo(json.dumps(proposal.to_dict(), indent=2))
        return
    for drifted in proposal.drift:
        click.echo(f"Hand edit kept: {drifted}")
    if proposal.version is None:
        click.echo(f"No proposal: {proposal.reason}.")
        return
    click.echo(f"Proposed {proposal.version} (installed: {proposal.base}) from {proposal.sessions} session(s):")
    for change in proposal.changes:
        click.echo(f"  {change.kind} {change.target}: {change.reason}")
    click.echo(f"Report: {proposal.report}")
    click.echo(f"Progress: {proposal.progress}")
    click.echo("See the diff with `thearc evolve diff`; then `thearc evolve apply` or `thearc evolve discard`.")


@evolve.command("diff")
@_json_option
@click.pass_obj
@_errors
def diff_command(where: dict[str, Any], as_json: bool) -> None:
    """Show the open proposal against the installed version."""
    from .round import diff

    proposal = diff(where["project"])
    if as_json:
        click.echo(json.dumps(proposal.to_dict(), indent=2))
        return
    for change in proposal.changes:
        click.echo(f"# {change.kind} {change.target} — {change.author}: {change.reason}")
    click.echo(proposal.diff)


@evolve.command("amend")
@click.option("-m", "--message", required=True, help="Why: the reason for the amendment.")
@click.pass_obj
@_errors
def amend_command(where: dict[str, Any], message: str) -> None:
    """Commit the edits made in the workspace to the open proposal."""
    from .round import amend

    for change in amend(where["project"], message):
        click.echo(change)


@evolve.command("apply")
@_json_option
@click.option("--push", is_flag=True, help="Push the workspace's main and the release to its remote.")
@click.pass_obj
@_errors
def apply_command(where: dict[str, Any], as_json: bool, push: bool) -> None:
    """Release the open proposal and install it."""
    from .round import apply

    applied = apply(where["project"], push=push)
    if as_json:
        click.echo(json.dumps(applied.to_dict(), indent=2))
        return
    click.echo(f"Installed {applied.version} for {', '.join(applied.harnesses)}; it takes effect in new sessions.")
    click.echo(f"Pushed the workspace to {applied.pushed}." if applied.pushed else
               "The workspace was not pushed (`thearc evolve apply --push` does).")
    click.echo("Commit the installed files and .thearc/workspace (the submodule) together.")


@evolve.command("discard")
@click.pass_obj
@_errors
def discard_command(where: dict[str, Any]) -> None:
    """Drop the open proposal."""
    from .round import discard

    click.echo(f"Discarded {discard(where['project'])}.")


@evolve.command("ignore")
@click.argument("patterns", nargs=-1, required=True)
@click.option("--remove", is_flag=True, help="Stop ignoring them: adopt them again from the installation.")
@click.pass_obj
@_errors
def ignore_command(where: dict[str, Any], patterns: tuple[str, ...], remove: bool) -> None:
    """Leave targets out of evolution: PATTERNS such as skill:vendor-* or "context:CLAUDE.md#Private notes"."""
    from .round import ignore

    agent = ignore(where["project"], list(patterns), remove=remove)
    click.echo(f"Ignored: {', '.join(agent.ignore) or 'nothing'}.")


@evolve.command("rollback")
@click.argument("version")
@click.pass_obj
@_errors
def rollback_command(where: dict[str, Any], version: str) -> None:
    """Install release VERSION again, as a new version."""
    from .round import rollback

    applied = rollback(where["project"], version)
    click.echo(f"Installed {version}'s configuration as {applied.version} for {', '.join(applied.harnesses)}.")


@evolve.command("status")
@_json_option
@click.pass_obj
@_errors
def status_command(where: dict[str, Any], as_json: bool) -> None:
    """Show the installed version, any open proposal, and what is evolved and ignored."""
    from .round import status

    state = status(where["project"])
    if as_json:
        click.echo(json.dumps(state, indent=2))
        return
    for key, value in state.items():
        click.echo(f"{key}: {', '.join(value) if isinstance(value, list) else value}")


@evolve.command("init")
@click.pass_obj
@_errors
def init_command(where: dict[str, Any]) -> None:
    """Check out the workspace submodule (after cloning a project set up for evolution)."""
    from .setup import init_from_remote

    agent = init_from_remote(where["project"])
    click.echo(f"Checked out {agent.name} {agent.version}.")


@evolve.command("remote")
@click.argument("url")
@click.pass_obj
@_errors
def remote_command(where: dict[str, Any], url: str) -> None:
    """Point the workspace at URL (its origin, and the project's submodule)."""
    from .setup import set_remote

    set_remote(where["project"], url)
    click.echo(f"The workspace's remote is {url}; commit .gitmodules, and `thearc evolve apply --push` pushes to it.")
