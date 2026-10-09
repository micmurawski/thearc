import json
import tomllib
from pathlib import Path

import pytest

from thearc.resources import (
    HARNESSES,
    MCP,
    ContextDocument,
    Hook,
    InstallConflict,
    MetaAgent,
    install,
    read_installation,
)
from thearc.resources.markdown import split_frontmatter

FASTAPI = Path(__file__).resolve().parent / "fastapi"


@pytest.mark.parametrize("harness", sorted(HARNESSES))
def test_install_then_import_round_trips(agent, harness, tmp_path):
    result = install(agent, harness, tmp_path)
    imported = read_installation(harness, tmp_path)
    if harness == "pi":  # Pi has no hooks or MCP servers.
        assert result.skipped == ("hook:guard", "hook:lint", "mcp:docs")
        agent = agent.model_copy(update={"hooks": {}, "mcps": {}})
    assert imported.revision == agent.revision
    assert (imported.name, imported.version) == ("demo", "1")


@pytest.mark.parametrize("harness", sorted(HARNESSES))
def test_skills_use_standard_frontmatter(agent, harness, tmp_path):
    install(agent, harness, tmp_path)
    skill = tmp_path / HARNESSES[harness].location("skills") / "retry" / "SKILL.md"
    assert skill.read_text().startswith("---\nname: retry\ndescription: Retry things\n---\n")


def test_claude_layout(agent, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(json.dumps({"model": "opus"}))
    install(agent, "claude", tmp_path)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"] == [{"hooks": [{"type": "command", "command": "./guard.sh"}]}]
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {
        "mcpServers": {"docs": {"type": "http", "url": "https://example.test/mcp"}}
    }
    assert (tmp_path / "AGENTS.md").is_file() and (tmp_path / ".claude/rules/style.md").is_file()


def test_reinstall_replaces_only_what_it_owns(agent, tmp_path):
    install(agent, "claude", tmp_path)
    settings = tmp_path / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"].append({"matcher": "Edit", "hooks": [{"type": "command", "command": "mine"}]})
    settings.write_text(json.dumps(data))
    (tmp_path / ".claude/rules/user.md").write_text("mine\n")

    smaller = agent.model_copy(deep=True)
    smaller.resources = {}
    smaller.hooks = {"lint": smaller.hooks["lint"]}
    install(smaller, "claude", tmp_path)

    assert not (tmp_path / ".claude/rules/style.md").exists()
    assert (tmp_path / ".claude/rules/user.md").read_text() == "mine\n"
    hooks = json.loads(settings.read_text())["hooks"]
    assert hooks["PreToolUse"] == [{"matcher": "Edit", "hooks": [{"type": "command", "command": "mine"}]}]
    assert hooks["PostToolUse"][0]["hooks"][0]["command"] == "ruff"


def test_hook_names_survive_through_the_marker(agent, tmp_path):
    install(agent, "codex", tmp_path)
    assert set(read_installation("codex", tmp_path).hooks) == {"guard", "lint"}


def test_unowned_files_are_conflicts_unless_forced(agent, tmp_path):
    rule = tmp_path / ".claude/rules/style.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("hand-written\n")
    with pytest.raises(InstallConflict, match="style.md"):
        install(agent, "claude", tmp_path)
    assert rule.read_text() == "hand-written\n"
    install(agent, "claude", tmp_path, force=True)
    assert rule.read_text() == agent.resources["rules/style.md"].content


USER_AGENTS = """# Project

Use uv.

## graphify

Old graphify instructions.

## Testing

Run pytest.
"""


def test_context_sections_are_merged_into_existing_documents(tmp_path):
    (tmp_path / "AGENTS.md").write_text(USER_AGENTS)
    agent = MetaAgent(
        name="g",
        context=[
            ContextDocument(
                filename="AGENTS.md", content="## graphify\n\nNew graphify instructions.\n\n## Style\n\nBe brief.\n"
            )
        ],
    )
    result = install(agent, "codex", tmp_path)
    assert result.context == {"AGENTS.md": ("graphify", "Style")}
    assert (
        (tmp_path / "AGENTS.md").read_text()
        == """# Project

Use uv.

## graphify

New graphify instructions.

## Testing

Run pytest.

## Style

Be brief.
"""
    )
    assert read_installation("codex", tmp_path).context["AGENTS.md"].content == agent.context["AGENTS.md"].content


def test_reinstall_removes_dropped_sections_and_keeps_user_content(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(USER_AGENTS)
    two = MetaAgent(name="g", context=[ContextDocument(filename="CLAUDE.md", content="## A\n\na\n\n## B\n\nb\n")])
    install(two, "claude", tmp_path)
    one = MetaAgent(name="g", context=[ContextDocument(filename="CLAUDE.md", content="## A\n\na2\n")])
    install(one, "claude", tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "## B" not in text and "a2" in text and "Use uv." in text and "Run pytest." in text
    install(MetaAgent(name="g"), "claude", tmp_path)
    assert (tmp_path / "CLAUDE.md").read_text().rstrip() == USER_AGENTS.rstrip()


def test_context_created_by_thearc_is_owned_whole(agent, tmp_path):
    result = install(agent, "claude", tmp_path)
    assert result.context == {"AGENTS.md": None}
    install(MetaAgent(name="empty"), "claude", tmp_path)
    assert not (tmp_path / "AGENTS.md").exists()


def test_codex_mcp_servers_are_toml_tables(tmp_path):
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('model = "gpt-5"\n\n[mcp_servers.theirs]\ncommand = "x"\n')
    agent = MetaAgent(
        name="a",
        mcps=[
            MCP(name="docs", command="npx", args=["-y", "docs"], env={"TOKEN": "t"}),
            MCP(name="web", url="https://example.test/mcp", transport="http", disabled=True),
        ],
    )
    install(agent, "codex", tmp_path)
    data = tomllib.loads(config.read_text())
    assert data["model"] == "gpt-5" and data["mcp_servers"]["theirs"] == {"command": "x"}
    assert data["mcp_servers"]["docs"] == {"command": "npx", "args": ["-y", "docs"], "env": {"TOKEN": "t"}}
    assert data["mcp_servers"]["web"] == {"url": "https://example.test/mcp", "enabled": False}
    assert read_installation("codex", tmp_path).mcps["web"] == agent.mcps["web"]

    install(MetaAgent(name="a"), "codex", tmp_path)
    assert tomllib.loads(config.read_text()) == {"model": "gpt-5", "mcp_servers": {"theirs": {"command": "x"}}}


def test_codex_unowned_mcp_server_is_a_conflict(tmp_path):
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.docs]\ncommand = "theirs"\n')
    agent = MetaAgent(name="a", mcps=[MCP(name="docs", command="ours")])
    with pytest.raises(InstallConflict, match="docs"):
        install(agent, "codex", tmp_path)
    install(agent, "codex", tmp_path, force=True)
    assert tomllib.loads(config.read_text())["mcp_servers"] == {"docs": {"command": "ours"}}


def test_uninstalling_everything_removes_owned_config(agent, tmp_path):
    install(agent, "claude", tmp_path)
    install(MetaAgent(name="empty"), "claude", tmp_path)
    remaining = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert remaining == [".claude/.thearc-agent.json"]


def test_script_hooks_are_installed_as_executable_files(tmp_path):
    agent = MetaAgent(name="a", hooks=[Hook(name="check", script="#!/bin/sh\nexit 0\n")])
    install(agent, "claude", tmp_path)
    script = tmp_path / ".claude/hooks/check.sh"
    assert script.stat().st_mode & 0o111
    command = json.loads((tmp_path / ".claude/settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert command["command"] == "./.claude/hooks/check.sh"


def test_unknown_harness():
    with pytest.raises(ValueError, match="supported"):
        install(MetaAgent(name="a"), "claudecode", ".")


@pytest.mark.skipif(not (FASTAPI / ".agents").is_dir(), reason="tests/fastapi submodule not checked out")
def test_imports_the_graphify_reference_project(tmp_path):
    antigravity = read_installation("antigravity", FASTAPI, name="graphify")
    codex = read_installation("codex", FASTAPI, name="graphify")
    assert antigravity.skills["graphify"].files[".graphify_version"]
    assert set(antigravity.resources) == {"rules/graphify.md", "workflows/graphify.md"}
    hook = next(iter(codex.hooks.values()))
    assert (hook.event, hook.matcher, hook.command) == ("pre_tool_use", "Bash", "graphify hook-check")

    combined = antigravity.model_copy(update={"hooks": codex.hooks})
    install(combined, "codex", tmp_path)
    assert json.loads((tmp_path / ".codex/hooks.json").read_text()) == json.loads(
        (FASTAPI / ".codex/hooks.json").read_text()
    )
    source = split_frontmatter((FASTAPI / ".agents/skills/graphify/SKILL.md").read_text())
    installed = split_frontmatter((tmp_path / ".codex/skills/graphify/SKILL.md").read_text())
    assert installed[:2] == source[:2]  # Same frontmatter data and exact body; YAML quoting may differ.
    assert installed[2] == source[2]  # Long values stay on one line, so line numbers agree.


# --- hooks per harness, and hooks owned by pattern ----------------------------------------------------------------

GUARD_SEARCH = Hook(name="guard-search", matcher="Bash|Grep", command="graphify hook-guard search",
                    harnesses=["claude"], extra={"timeout": 10})
GUARD_READ = Hook(name="guard-read", matcher="Read|Glob", command="graphify hook-guard read",
                  harnesses=["claude"], extra={"timeout": 10})
CHECK = Hook(name="check", matcher="Bash", command="graphify hook-check", harnesses=["codex"])
GRAPHIFY = MetaAgent(name="graphify", version="0.9.68", owns_hooks=["graphify *"],
                     hooks=[GUARD_SEARCH, GUARD_READ, CHECK])


def native_hooks(path: Path) -> list[tuple]:
    return [(event, group.get("matcher"), hook["command"])
            for event, groups in json.loads(path.read_text())["hooks"].items()
            for group in groups for hook in group["hooks"]]


def test_hooks_install_only_into_their_harnesses(tmp_path):
    claude, codex = install(GRAPHIFY, "claude", tmp_path), install(GRAPHIFY, "codex", tmp_path)
    assert claude.hooks == ("guard-read", "guard-search") and codex.hooks == ("check",)
    assert native_hooks(tmp_path / ".codex/hooks.json") == [("PreToolUse", "Bash", "graphify hook-check")]
    assert install(GRAPHIFY, "pi", tmp_path / "pi").skipped == ()  # Nothing here was meant for Pi.
    assert install(MetaAgent(name="a", hooks=[Hook(name="all", command="x")]), "pi", tmp_path / "pi2").skipped == (
        "hook:all",)


def test_owned_hooks_installed_by_hand_are_replaced_and_others_kept(tmp_path):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    by_hand = json.loads((FASTAPI / ".claude/settings.json").read_text()) if (FASTAPI / ".claude").is_dir() else {
        "hooks": {"PreToolUse": [{"matcher": "Bash|Grep", "hooks": [
            {"type": "command", "command": "graphify hook-guard search", "timeout": 10}]}]}}
    by_hand["hooks"]["PreToolUse"].append({"matcher": "Edit", "hooks": [{"type": "command", "command": "mine"}]})
    by_hand["hooks"]["PreToolUse"][0]["hooks"].append({"type": "command", "command": "graphify old-guard"})
    settings.write_text(json.dumps(by_hand))

    newer = GRAPHIFY.model_copy(update={"hooks": {"guard-read": GUARD_READ}})
    install(newer, "claude", tmp_path)
    install(newer, "claude", tmp_path)  # Reinstalling changes nothing.
    assert native_hooks(settings) == [("PreToolUse", "Edit", "mine"),
                                      ("PreToolUse", "Read|Glob", "graphify hook-guard read")]


def test_owned_hooks_in_a_flat_hooks_file_are_replaced(tmp_path):
    hooks_file = tmp_path / ".agents/hooks.json"
    hooks_file.parent.mkdir()
    hooks_file.write_text(json.dumps({"old": {"command": "graphify hook-old"}, "mine": {"command": "mine"}}))
    check = Hook(name="check", command="graphify hook-check")
    install(MetaAgent(name="graphify", owns_hooks=["graphify *"], hooks=[check]), "antigravity", tmp_path)
    assert json.loads(hooks_file.read_text()) == {"mine": {"command": "mine"},
                                                  "check": {"command": "graphify hook-check"}}
    imported = read_installation("antigravity", tmp_path)
    assert imported.hooks["check"].harnesses == [] and imported.hooks["mine"].harnesses == ["antigravity"]
    assert imported.owns_hooks == ["graphify *"]


def test_scopes_and_ownership_survive_install_and_import(tmp_path):
    install(GRAPHIFY, "claude", tmp_path)
    imported = read_installation("claude", tmp_path, name="graphify", version="0.9.68")
    assert imported.hooks == {"guard-search": GUARD_SEARCH, "guard-read": GUARD_READ}
    assert imported.owns_hooks == ["graphify *"]


@pytest.mark.skipif(not (FASTAPI / ".claude").is_dir(), reason="tests/fastapi submodule not checked out")
def test_graphify_hooks_of_each_harness_combine_into_one_agent(tmp_path):
    claude = read_installation("claude", FASTAPI, name="graphify")
    codex = read_installation("codex", FASTAPI, name="graphify")
    assert {hook.harnesses[0] for hook in claude.hooks.values()} == {"claude"} and len(claude.hooks) == 2
    combined = codex.model_copy(update={"hooks": {**claude.hooks, **codex.hooks}, "owns_hooks": ["graphify *"]})
    for harness, native in [("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")]:
        project = tmp_path / harness
        target = project / native
        target.parent.mkdir(parents=True)
        target.write_text((FASTAPI / native).read_text())  # Graphify's own hand-made install is already there.
        install(combined, harness, project)
        assert json.loads(target.read_text()) == json.loads((FASTAPI / native).read_text())
