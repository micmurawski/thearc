import json
import tempfile
from pathlib import Path

from thearc import (
    MCP,
    ContextDocument,
    Hook,
    MetaAgent,
    Skill,
)


def test_agent_initialization():
    skill = Skill(name="unit-test-skill", description="Testing skill", instructions="Echo test")
    hook = Hook(name="pre-tool", event="pre_tool_use", command="echo hook")
    mcp = MCP(name="filesystem", command="npx", args=["server-fs"])
    ctx_agents = ContextDocument(filename="AGENTS.md", content="# Agents context")
    ctx_claude = ContextDocument(filename="CLAUDE.md", content="# Claude context")

    agent = MetaAgent(
        name="test-agent",
        skills=[skill],
        hooks=[hook],
        mcps=[mcp],
        context=[ctx_agents, ctx_claude],
    )

    assert agent.name == "test-agent"
    assert isinstance(agent.skills, dict)
    assert isinstance(agent.hooks, dict)
    assert isinstance(agent.mcps, dict)
    assert isinstance(agent.context, dict)

    # Check object representation
    assert len(agent.skills) == 1
    assert agent.skills["unit-test-skill"].description == "Testing skill"

    assert len(agent.hooks) == 1
    assert agent.hooks["pre-tool"].event == "pre_tool_use"

    assert len(agent.mcps) == 1
    assert agent.mcps["filesystem"].command == "npx"

    assert len(agent.context) == 2
    assert agent.context["AGENTS.md"].content == "# Agents context"
    assert agent.context["CLAUDE.md"].content == "# Claude context"



def test_agent_initialization_with_dicts_and_context():
    agent = MetaAgent(
        name="dict-agent",
        skills=[{"name": "dict-skill", "instructions": "Do something"}],
        hooks=[{"name": "dict-hook", "event": "post_tool_use"}],
        mcps=[{"name": "dict-mcp", "command": "python", "args": ["-m", "mcp"]}],
        context={"AGENTS.md": "# Quick Agents Note"},
    )

    assert isinstance(agent.skills["dict-skill"], Skill)
    assert isinstance(agent.hooks["dict-hook"], Hook)
    assert isinstance(agent.mcps["dict-mcp"], MCP)
    assert isinstance(agent.context["AGENTS.md"], ContextDocument)
    assert agent.context["AGENTS.md"].content == "# Quick Agents Note"


def test_agent_install_claudecode():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)
        agent = MetaAgent(
            name="claude-agent",
            skills=[Skill(name="claude-skill", description="Claude Skill", instructions="Use Claude")],
            hooks=[Hook(name="claude-hook", event="pre_tool_use", command="claude-cmd")],
            mcps=[MCP(name="claude-mcp", command="claude-mcp-cmd")],
            context=[ContextDocument(filename="CLAUDE.md", content="# Claude Guidelines\nInitial content")],
        )

        res = agent.install(implementation="claudecode", replace=True, project_dir=proj_dir)

        assert len(res["skills"]) >= 1
        skill_file = proj_dir / ".claude" / "skills" / "claude-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "Use Claude" in skill_file.read_text()

        hooks_file = proj_dir / ".claude" / "hooks.json"
        assert hooks_file.exists()
        hooks_data = json.loads(hooks_file.read_text())
        assert "claude-hook" in hooks_data

        mcp_file = proj_dir / ".claude" / "mcp.json"
        assert mcp_file.exists()
        mcp_data = json.loads(mcp_file.read_text())
        assert "claude-mcp" in mcp_data["mcpServers"]

        ctx_file = proj_dir / "CLAUDE.md"
        assert ctx_file.exists()
        assert "Claude Guidelines" in ctx_file.read_text()


def test_agent_install_codex():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)
        agent = MetaAgent(
            name="codex-agent",
            skills=[Skill(name="codex-skill", description="Codex Skill", instructions="Codex Instruct")],
            hooks=[Hook(name="codex-hook", event="pre_tool_use", script="echo codex")],
            mcps=[MCP(name="codex-mcp", command="codex-cmd")],
            context=[ContextDocument(filename="AGENTS.md", content="# Codex Rules\nFollow formatting")],
        )

        agent.install(implementation="codex", replace=True, project_dir=proj_dir)

        skill_file = proj_dir / ".codex" / "skills" / "codex-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "Codex Instruct" in skill_file.read_text()

        mcp_file = proj_dir / ".codex" / "mcp.json"
        assert mcp_file.exists()

        ctx_file = proj_dir / "AGENTS.md"
        assert ctx_file.exists()
        assert "Codex Rules" in ctx_file.read_text()


def test_agent_install_anitgravity():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)
        agent = MetaAgent(
            name="antigravity-agent",
            skills=[Skill(name="ag-skill", description="AG Skill", instructions="AG Instruct")],
            hooks=[Hook(name="ag-hook", event="pre_tool_use", command="ag-cmd")],
            mcps=[MCP(name="ag-mcp", command="ag-mcp-cmd")],
            context=[ContextDocument(filename="AGENTS.md", content="# Antigravity Rules\nBe awesome")],
        )

        agent.install(implementation="anitgravity", replace=True, project_dir=proj_dir)

        skill_file = proj_dir / ".agents" / "skills" / "ag-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "AG Instruct" in skill_file.read_text()

        mcp_file = proj_dir / ".agents" / "mcp_config.json"
        assert mcp_file.exists()

        ctx_file = proj_dir / "AGENTS.md"
        assert ctx_file.exists()


def test_agent_install_pi():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)
        agent = MetaAgent(
            name="pi-agent",
            skills=[Skill(name="pi-skill", description="Pi Skill", instructions="Pi Instruct")],
            hooks=[Hook(name="pi-hook", event="pre_tool_use", command="pi-cmd")],
            mcps=[MCP(name="pi-mcp", command="pi-mcp-cmd")],
            context=[ContextDocument(filename="PI.md", content="# Pi Rules\nRun pi agent")],
        )

        agent.install(implementation="pi", replace=True, project_dir=proj_dir)

        skill_file = proj_dir / ".pi" / "skills" / "pi-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "Pi Instruct" in skill_file.read_text()

        mcp_file = proj_dir / ".pi" / "mcp.json"
        assert mcp_file.exists()

        ctx_file = proj_dir / "PI.md"
        assert ctx_file.exists()
        assert "Pi Rules" in ctx_file.read_text()


def test_replace_false_appends_and_merges():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)

        # 1. First agent install
        agent1 = MetaAgent(
            skills=[
                Skill(name="shared-skill", description="Initial skill", 
                          instructions="# Section One\nFirst skill content.")],
            hooks=[Hook(name="hook1", command="echo 1")],
            mcps=[MCP(name="mcp1", command="cmd1")],
            context=[
                ContextDocument(filename="AGENTS.md", 
                                content="# General Standards\nUse Python.\n\n# Code Quality\nClean code.")],
        )
        agent1.install(implementation="claudecode", replace=True, project_dir=proj_dir)

        # Verify initial state
        ctx_file = proj_dir / "AGENTS.md"
        initial_ctx = ctx_file.read_text()
        assert "# General Standards" in initial_ctx
        assert "# Code Quality" in initial_ctx

        mcp_file = proj_dir / ".claude" / "mcp.json"
        mcp_data1 = json.loads(mcp_file.read_text())
        assert "mcp1" in mcp_data1["mcpServers"]
        assert "mcp2" not in mcp_data1["mcpServers"]

        # 2. Second agent install with replace=False (should APPEND/MERGE)
        agent2 = MetaAgent(
            skills=[
                Skill(name="shared-skill", description="Initial skill", 
                      instructions="# Section Two\nSecond skill section.")
            ],
            hooks=[Hook(name="hook2", command="echo 2")],
            mcps=[MCP(name="mcp2", command="cmd2")],
            context=[ContextDocument(
                filename="AGENTS.md", 
                content="# Security Rules\nNever leak keys.\n\n# Code Quality\nUpdated quality rules.")
            ],
        )
        agent2.install(implementation="claudecode", replace=False, project_dir=proj_dir)

        # Check Context Document: # General Standards preserved, # Security Rules appended, # Code Quality updated
        merged_ctx = ctx_file.read_text()
        assert "# General Standards" in merged_ctx
        assert "# Security Rules" in merged_ctx
        assert "Updated quality rules." in merged_ctx

        # Check MCPs: mcp1 AND mcp2 both present
        mcp_data2 = json.loads(mcp_file.read_text())
        assert "mcp1" in mcp_data2["mcpServers"]
        assert "mcp2" in mcp_data2["mcpServers"]

        # Check Hooks: hook1 AND hook2 both present
        hooks_file = proj_dir / ".claude" / "hooks.json"
        hooks_data2 = json.loads(hooks_file.read_text())
        assert "hook1" in hooks_data2
        assert "hook2" in hooks_data2

        # Check Skill: Section One AND Section Two present
        skill_file = proj_dir / ".claude" / "skills" / "shared-skill" / "SKILL.md"
        skill_text = skill_file.read_text()
        assert "Section One" in skill_text or "First skill content" in skill_text
        assert "Section Two" in skill_text or "Second skill section" in skill_text


def test_replace_true_overwrites_completely():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir)

        # Initial install
        agent1 = MetaAgent(
            mcps=[MCP(name="mcp1", command="cmd1")],
            context=[ContextDocument(filename="AGENTS.md", content="# Old Content\nOld rules.")],
        )
        agent1.install(implementation="claudecode", replace=True, project_dir=proj_dir)

        # Overwrite install with replace=True
        agent2 = MetaAgent(
            mcps=[MCP(name="mcp2", command="cmd2")],
            context=[ContextDocument(filename="AGENTS.md", content="# Completely New Content\nBrand new.")],
        )
        agent2.install(implementation="claudecode", replace=True, project_dir=proj_dir)

        # Context should be completely replaced
        ctx_file = proj_dir / "AGENTS.md"
        ctx_text = ctx_file.read_text()
        assert "# Old Content" not in ctx_text
        assert "# Completely New Content" in ctx_text

        # MCP should be completely replaced
        mcp_file = proj_dir / ".claude" / "mcp.json"
        mcp_data = json.loads(mcp_file.read_text())
        assert "mcp1" not in mcp_data["mcpServers"]
        assert "mcp2" in mcp_data["mcpServers"]


def test_agent_install_with_explicit_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        custom_path = Path(tmpdir) / "custom_target_dir"
        agent = MetaAgent(
            skills=[Skill(name="path-skill", instructions="Path skill instructions")],
            context=[ContextDocument(filename="AGENTS.md", content="# Path Test")],
        )

        agent.install(implementation="claudecode", replace=True, path=custom_path)

        assert custom_path.exists()
        assert (custom_path / "AGENTS.md").exists()
        assert (custom_path / ".claude" / "skills" / "path-skill" / "SKILL.md").exists()
