"""The ``thearcv2`` command line."""

import click

from thearcv2.sessions.cli import sessions


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main():
    """thearc v2: collect and inspect agent sessions."""


main.add_command(sessions)
