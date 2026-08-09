import pytest
from pathlib import Path
from thearc.core.installer import PluginInstaller

def test_list_bundled():
    installer = PluginInstaller()
    skills = installer.list_bundled_skills()
    commands = installer.list_bundled_commands()
    assert "uploadcontext" in skills
    assert "uploadcontext" in commands

def test_install_local_project(tmp_path):
    installer = PluginInstaller()
    results = installer.install(
        target_scope="project",
        agent_name="all",
        project_dir=tmp_path,
        force=True
    )
    assert "antigravity" in results
    assert "claude" in results
    assert "codex" in results

    # Verify AGY skill file
    agy_skill = tmp_path / ".agents" / "skills" / "uploadcontext" / "SKILL.md"
    assert agy_skill.exists()

    # Verify Claude skill file
    claude_skill = tmp_path / ".claude" / "skills" / "uploadcontext" / "SKILL.md"
    assert claude_skill.exists()

    # Verify Codex skill file
    codex_skill = tmp_path / ".codex" / "skills" / "uploadcontext" / "SKILL.md"
    assert codex_skill.exists()
