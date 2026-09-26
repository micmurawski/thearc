import click

from thearc import __version__
from thearc.history.cli import history


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="thearc")
def main():
    """thearc: Agent-agnostic plugin & skill installer system for Codex, Claude, and Antigravity."""


main.add_command(history)

if __name__ == "__main__":
    main()
