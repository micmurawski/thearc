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

@pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
def test_thearc_toml_generation_and_loading(tmp_path, agent):
    installer = get_installer(agent)
    cfg_file = installer.ensure_config_file(target_scope="project", project_dir=tmp_path, force=True)
    
    assert cfg_file.exists()
    assert cfg_file.name == ".thearc.toml"
    assert cfg_file.parent == installer.get_local_paths(tmp_path)["base"]

    config_data = load_thearc_config(project_dir=tmp_path, agent=agent)
    assert config_data == {
        "version": "0.1.0",
        "agent": {"name": installer.name},
        "cli": {"default_scope": "project", "default_agent": installer.name},
    }
