import pytest
from pathlib import Path
from thearc.agents import get_installer, get_all_installers
from thearc.agents.antigravity import AntigravityInstaller
from thearc.agents.claude import ClaudeInstaller
from thearc.agents.codex import CodexInstaller

def test_get_installer():
    assert isinstance(get_installer("antigravity"), AntigravityInstaller)
    assert isinstance(get_installer("claude"), ClaudeInstaller)
    assert isinstance(get_installer("codex"), CodexInstaller)

def test_get_installer_invalid():
    with pytest.raises(ValueError):
        get_installer("non_existent")

def test_get_all_installers():
    installers = get_all_installers()
    assert len(installers) == 3
    names = [i.name for i in installers]
    assert "antigravity" in names
    assert "claude" in names
    assert "codex" in names

def test_format_skill_file():
    ag = AntigravityInstaller()
    formatted_ag = ag.format_skill_file("testskill", "Content here")
    assert "name: testskill" in formatted_ag

    cl = ClaudeInstaller()
    formatted_cl = cl.format_skill_file("testskill", "Content here")
    assert "# Skill: testskill" in formatted_cl

    cx = CodexInstaller()
    formatted_cx = cx.format_skill_file("testskill", "Content here")
    assert "<!-- Skill: testskill" in formatted_cx
