import pytest
from pathlib import Path
from thearc.config import (
    AGENT_ANTIGRAVITY,
    AGENT_CLAUDE,
    AGENT_CODEX,
    SUPPORTED_AGENTS,
    get_global_agent_paths,
    get_local_agent_paths,
)

def test_supported_agents():
    assert "antigravity" in SUPPORTED_AGENTS
    assert "claude" in SUPPORTED_AGENTS
    assert "codex" in SUPPORTED_AGENTS

def test_global_paths():
    for agent in SUPPORTED_AGENTS:
        paths = get_global_agent_paths(agent)
        assert "skills" in paths
        assert "rules" in paths
        assert "commands" in paths

def test_local_paths(tmp_path):
    for agent in SUPPORTED_AGENTS:
        paths = get_local_agent_paths(tmp_path, agent)
        assert paths["skills"].is_relative_to(tmp_path)
        assert paths["rules"].is_relative_to(tmp_path)
        assert paths["commands"].is_relative_to(tmp_path)

def test_invalid_agent():
    with pytest.raises(ValueError):
        get_global_agent_paths("unknown_agent")
