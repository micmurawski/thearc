import click

from thearc import __version__
from thearc.learning.cli import evidence, handoff_commands, history, sessions
from thearc.learning.curation.cli import curation
from thearc.learning.reflection.cli import reflection


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="thearc")
def main():
    """thearc: Inspect sessions, prepare evidence, reflect on configurations, and curate agents."""


main.add_command(sessions)
main.add_command(evidence)
main.add_command(handoff_commands)
main.add_command(history)
main.add_command(curation)
main.add_command(reflection)

if __name__ == "__main__":
    main()
