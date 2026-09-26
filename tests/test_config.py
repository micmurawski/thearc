import pytest

from thearc.agents import get_installer
from thearc.config import (
    SUPPORTED_AGENTS,
    get_global_agent_paths,
    get_local_agent_paths,
    load_thearc_config,
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

def test_thearc_toml_generation_and_loading(tmp_path):
    claude = get_installer("claude")
    cfg_file = claude.ensure_config_file(target_scope="project", project_dir=tmp_path, force=True)
    
    assert cfg_file.exists()
    assert cfg_file.name == ".thearc.toml"
    assert cfg_file.parent.name == ".claude"

    config_data = load_thearc_config(project_dir=tmp_path, agent="claude")
    assert config_data.get("agent", {}).get("name") == "claude"
    assert "uploadcontext" in config_data
