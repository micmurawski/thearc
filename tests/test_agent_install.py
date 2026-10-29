"""``thearc agent install``: a MetaAgent from its workspace repository, at a release, into a project or a harness's
own folder."""

import json

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.resources import ContextDocument, Hook
from thearc.resources.harness import global_layout, installed_agents
from thearc.resources.source import agent_from, is_url


@pytest.fixture
def repository(agent, tmp_path):
    """A workspace released twice, 1.0.1 and 1.0.2 (``main``), with a CLAUDE.md and a hook script."""
    rules = ContextDocument(filename="CLAUDE.md", content="# Rules\n\nBe brief.\n")
    configuration = agent.model_copy(update={"version": "1.0.0", "context": {"CLAUDE.md": rules},
                                             "hooks": {"check": Hook(name="check", script="#!/bin/sh\necho ok\n")}})
    workspace = configuration.init_workspace(tmp_path / "workspace")
    workspace.release(part="patch").release(part="patch")
    return tmp_path / "workspace"


def run(*args, **env):
    result = CliRunner().invoke(main, ["agent", *args], env=env or None)
    return result


def test_a_release_installs_into_a_project(repository, tmp_path):
    project = tmp_path / "project"
    result = run("install", "--path", str(repository), "--version", "1.0.1", "--harness", "claude",
                 "--project", str(project))
    assert result.exit_code == 0, result.output
    assert "Installed demo 1.0.1 for claude" in result.output
    assert installed_agents(project) == {"demo": {"claude": "1.0.1"}}
    assert (project / ".claude/skills/retry/SKILL.md").is_file() and "Be brief." in (project / "CLAUDE.md").read_text()


def test_main_installs_without_a_version_and_a_url_is_cloned(repository, tmp_path):
    url = f"file://{repository}"
    assert is_url(url) and is_url("git@github.com:me/agent.git") and not is_url(str(repository))
    assert agent_from(url).version == "1.0.2" and agent_from(url, "1.0.1").version == "1.0.1"
    result = run("install", "--url", url, "--harness", "codex", "--project", str(tmp_path / "project"))
    assert result.exit_code == 0, result.output
    assert installed_agents(tmp_path / "project") == {"demo": {"codex": "1.0.2"}}


def test_an_unknown_release_names_the_known_ones(repository, tmp_path):
    result = run("install", "--repo", str(repository), "--version", "9.9.9", "--harness", "claude",
                 "--project", str(tmp_path / "project"))
    assert result.exit_code != 0 and "has no release 9.9.9; releases: 1.0.1, 1.0.2" in result.output


def test_a_global_install_goes_into_the_harness_folder(repository, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    result = run("install", "--path", str(repository), "--version", "1.0.2", "--harness", "claude", "--global")
    assert result.exit_code == 0, result.output
    root = (home / ".claude").resolve()
    assert installed_agents(root) == {"demo": {"claude": "1.0.2"}}
    assert (root / "skills/retry/SKILL.md").is_file() and "Be brief." in (root / "CLAUDE.md").read_text()
    assert not (home / "CLAUDE.md").exists()
    commands = [hook["command"] for entry in json.loads((root / "settings.json").read_text())["hooks"].values()
                for group in entry for hook in group["hooks"]]
    assert f"{root}/hooks/check.sh" in commands and (root / "hooks/check.sh").is_file()  # Run by its absolute path.
    assert "mcp:docs" in result.output  # Claude's own MCP servers live outside ~/.claude.

    result = run("uninstall", "--name", "demo", "--harness", "claude", "--global")
    assert result.exit_code == 0, result.output
    assert installed_agents(root) == {} and not (root / "skills/retry").exists()


def test_global_layouts(tmp_path):
    root, layout = global_layout("codex", home=tmp_path)
    assert root == (tmp_path / ".codex").resolve() and layout.mcp_file == "config.toml"
    assert global_layout("pi", home=tmp_path)[0] == (tmp_path / ".pi/agent").resolve()
    assert global_layout("opencode", home=tmp_path)[0] == (tmp_path / ".config/opencode").resolve()
    with pytest.raises(ValueError, match="cannot install 'antigravity' globally"):
        global_layout("antigravity", home=tmp_path)  # type: ignore[arg-type]


def test_project_and_global_exclude_each_other(repository, tmp_path):
    result = run("install", "--path", str(repository), "--harness", "claude", "--project", str(tmp_path), "--global")
    assert result.exit_code != 0 and "not both" in result.output
