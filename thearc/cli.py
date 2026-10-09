"""The ``thearc`` command line."""

import click

from thearc.sessions.cli import sessions
from thearc.utils import AliasedGroup


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=AliasedGroup)
def main() -> None:
    """thearc v2: collect and inspect agent sessions."""


main.add_command(sessions)
