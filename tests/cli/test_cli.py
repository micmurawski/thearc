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
        (["reflection"], ["run", "handoff", "show"], []),
        (["curation"], ["run", "show", "history", "diff"], ["approve", "accept"]),
        (["sessions"], ["index", "sync", "search", "show", "trace", "list"],
         ["snapshot", "export", "inspect", "handoff"]),
        (["evidence"], ["snapshot", "export", "inspect"], ["handoff", "search"]),
        (["handoff"], ["prepare", "reflect", "show"], ["index", "sync"]),
    ],
)
def test_help_groups(path, visible, hidden):
    result = CliRunner().invoke(main, [*path, "--help"])
    assert result.exit_code == 0, result.output
    commands = {line.split()[0] for line in result.output.split("Commands:\n", 1)[1].splitlines() if line.strip()}
    assert commands == set(visible)
    assert commands.isdisjoint(hidden)


@pytest.mark.parametrize("path", [["handoff"], ["sessions", "handoff"], ["history", "handoff"]])
def test_handoff_paths_validate_saved_plans(tmp_path, path):
    result = CliRunner().invoke(main, [*path, "show", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "No such command" not in result.output


def test_history_search_alias(tmp_path):
    runner = CliRunner()
    index = str(tmp_path / "sessions.sqlite")
    canonical = runner.invoke(main, ["sessions", "--index", index, "list"])
    legacy = runner.invoke(main, ["history", "--index", index, "list"])
    assert canonical.exit_code == legacy.exit_code == 0
    assert canonical.output == legacy.output
