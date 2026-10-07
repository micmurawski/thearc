import pytest
from click.testing import CliRunner

from thearc.cli import main


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


@pytest.mark.parametrize(
    ("path", "visible", "hidden"),
    [
        ([], ["sessions", "evidence", "handoff", "curation", "reflection"], ["history"]),
        (["reflection"], ["run", "handoff", "serve", "resources", "show"], []),
        (["curation"], ["run", "show", "history", "diff"], ["approve", "accept"]),
        (["sessions"], ["add", "sources", "index", "sync", "search", "show", "trace", "list", "render"],
         ["snapshot", "export", "inspect", "handoff"]),
        (["evidence"], ["snapshot", "export", "inspect"], ["handoff", "search"]),
        (["handoff"], ["prepare", "show"], ["index", "sync"]),
    ],
)
def test_help_groups(path, visible, hidden):
    result = CliRunner().invoke(main, [*path, "--help"])
    assert result.exit_code == 0, result.output
    commands = {line.split()[0] for line in result.output.split("Commands:\n", 1)[1].splitlines() if line.strip()}
    assert commands == set(visible)
    assert commands.isdisjoint(hidden)


@pytest.mark.parametrize("path", [["handoff"]])
def test_handoff_paths_validate_saved_plans(tmp_path, path):
    result = CliRunner().invoke(main, [*path, "show", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "No such command" not in result.output


@pytest.mark.parametrize("path", [["history"], ["sessions", "snapshot"], ["sessions", "handoff"],
                                  ["handoff", "reflect"]])
def test_removed_aliases_are_not_registered(path):
    result = CliRunner().invoke(main, [*path, "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output
