import sys
import click
from pathlib import Path
from typing import Optional, Tuple

from thearc import __version__
from thearc.config import SUPPORTED_AGENTS, get_local_agent_paths, get_global_agent_paths
from thearc.core.installer import PluginInstaller
from thearc.core.history import find_latest_transcript
from thearc.core.s3_uploader import parse_attributes, upload_context_to_s3

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="thearc")
def main():
    """thearc: Agent-agnostic plugin & skill installer system for Codex, Claude, and Antigravity."""
    pass

@main.command(name="install")
@click.option("--project", "-p", is_flag=True, default=False, help="Install skills/commands into local project directory (.agents, .claude, .codex).")
@click.option("--global", "-g", "is_global", is_flag=True, default=False, help="Install skills/commands globally in user home directory.")
@click.option("--agent", "-a", type=click.Choice(SUPPORTED_AGENTS + ["all"], case_sensitive=False), default="all", help="Target agent(s) to install for.")
@click.option("--skill", "-s", default=None, help="Name of specific skill to install (default: all bundled skills).")
@click.option("--force", "-f", is_flag=True, default=False, help="Force overwrite existing installed files.")
@click.option("--project-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", help="Target project root directory.")
def install_cmd(project, is_global, agent, skill, force, project_dir):
    """Install bundled skills and commands into local project or global agent configurations."""
    # Determine target scope
    if is_global and project:
        click.echo("Error: Cannot specify both --project and --global simultaneously.", err=True)
        sys.exit(1)
    
    scope = "global" if is_global else "project"
    
    click.echo(f"🚀 Installing thearc plugins...")
    click.echo(f"   Scope:  {scope}")
    click.echo(f"   Agents: {agent}")
    if skill:
        click.echo(f"   Skill:  {skill}")
    click.echo()

    installer = PluginInstaller()

    try:
        results = installer.install(
            target_scope=scope,
            agent_name=agent,
            skill_name=skill,
            project_dir=project_dir,
            force=force
        )

        for ag, files in results.items():
            click.echo(click.style(f"=== {ag.upper()} ===", fg="cyan", bold=True))
            if not files:
                click.echo("   (No new files installed. Use --force to overwrite)")
            else:
                for f in files:
                    click.echo(f"   ✓ Installed: {f}")
            click.echo()

        click.echo(click.style("✨ Installation completed successfully!", fg="green", bold=True))

    except Exception as e:
        click.echo(click.style(f"❌ Error during installation: {e}", fg="red"), err=True)
        sys.exit(1)

@main.command(name="uploadcontext")
@click.argument("attributes_arg", required=False, default="")
@click.option("--attr", "-a", multiple=True, help="Additional key=value attributes (e.g. -a workspace=foo -a name=bar)")
@click.option("--file", "-f", "file_path", type=click.Path(path_type=Path), default=None, help="Path to transcript or context file.")
@click.option("--bucket", "-b", default=None, help="Target AWS S3 bucket name.")
@click.option("--prefix", default=None, help="S3 key folder prefix.")
@click.option("--agent", type=click.Choice(SUPPORTED_AGENTS, case_sensitive=False), default=None, help="Specific agent history to search.")
@click.option("--dry-run", is_flag=True, default=False, help="Simulate upload and output target S3 URI without executing S3 call.")
def uploadcontext_cmd(attributes_arg, attr, file_path, bucket, prefix, agent, dry_run):
    """Upload current chat history/transcript to S3 with key-value metadata attributes."""
    click.echo("📦 Preparing context upload...")

    # Combine positional attributes and --attr flags
    combined_attr_inputs = []
    if attributes_arg:
        combined_attr_inputs.append(attributes_arg)
    if attr:
        combined_attr_inputs.extend(attr)

    attributes = parse_attributes(" ".join(combined_attr_inputs))

    # Fallback default workspace/name if none supplied
    if "workspace" not in attributes:
        attributes["workspace"] = Path.cwd().name or "default"
    if "name" not in attributes:
        attributes["name"] = "chat_session"

    click.echo(f"   Attributes: {attributes}")

    # Resolve transcript file
    try:
        target_file = find_latest_transcript(agent=agent, custom_path=file_path)
        click.echo(f"   Transcript: {target_file}")
    except Exception as e:
        click.echo(click.style(f"❌ Error locating transcript: {e}", fg="red"), err=True)
        sys.exit(1)

    # Perform upload
    result = upload_context_to_s3(
        file_path=target_file,
        attributes=attributes,
        bucket=bucket,
        prefix=prefix,
        dry_run=dry_run
    )

    if result.get("uploaded"):
        click.echo(click.style(f"\n✅ {result['message']}", fg="green", bold=True))
    elif result.get("dry_run"):
        click.echo(click.style(f"\n🔍 {result['message']}", fg="yellow", bold=True))
    else:
        click.echo(click.style(f"\n⚠️  {result['message']}", fg="yellow"))

    click.echo(f"   S3 URI: {result['s3_uri']}")

@main.command(name="list")
def list_cmd():
    """List skills and commands bundled in thearc."""
    installer = PluginInstaller()
    skills = installer.list_bundled_skills()
    commands = installer.list_bundled_commands()

    click.echo(click.style("Bundled Skills:", fg="cyan", bold=True))
    for s in skills:
        click.echo(f"  • {s}")

    click.echo(click.style("\nBundled Slash Commands:", fg="cyan", bold=True))
    for c in commands:
        click.echo(f"  • /{c}")

@main.command(name="status")
@click.option("--project-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", help="Target project root directory.")
def status_cmd(project_dir):
    """Show installed skills and configurations for supported agents."""
    project_dir = Path(project_dir).resolve()
    click.echo(click.style(f"Project Directory: {project_dir}", bold=True))
    click.echo()

    for ag in SUPPORTED_AGENTS:
        click.echo(click.style(f"Agent: {ag.upper()}", fg="cyan", bold=True))
        
        local_paths = get_local_agent_paths(project_dir, ag)
        global_paths = get_global_agent_paths(ag)

        click.echo("  Local Paths:")
        for k, p in local_paths.items():
            status_str = "exists" if p.exists() else "not created"
            click.echo(f"    - {k}: {p} ({status_str})")

        click.echo("  Global Paths:")
        for k, p in global_paths.items():
            status_str = "exists" if p.exists() else "not created"
            click.echo(f"    - {k}: {p} ({status_str})")

        click.echo()

if __name__ == "__main__":
    main()
