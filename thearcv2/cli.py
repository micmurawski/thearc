"""The ``thearcv2`` command line."""

import click

from thearcv2.sessions.cli import sessions
from thearcv2.utils import AliasedGroup


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=AliasedGroup)
def main() -> None:
    """thearc v2: collect and inspect agent sessions."""


main.add_command(sessions)
