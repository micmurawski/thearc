"""Integration coverage for installing the FastAPI Graphify agent configuration.

The ``tests/fastapi`` submodule is the reference fixture: it contains Graphify as an
Antigravity skill and as a Codex skill, together with a native Codex hook and
the project context document.  This test deliberately installs that content
into empty projects, so it exercises the public meta-configuration API rather
than relying on any developer's local agent configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thearc import MetaAgent, Skill

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GRAPHIFY_ROOT = REPOSITORY_ROOT / "tests" / "fastapi"
GRAPHIFY_SKILL_DIR = GRAPHIFY_ROOT / ".agents" / "skills" / "graphify"
GRAPHIFY_CONTEXT_FILE = GRAPHIFY_ROOT / "AGENTS.md"
GRAPHIFY_HOOKS_FILE = GRAPHIFY_ROOT / ".codex" / "hooks.json"


TARGETS = {
    "antigravity": (".agents", "---\nname: graphify"),
    "claude": (".claude", "# Skill: graphify"),
    "codex": (".codex", "<!-- Skill: graphify"),
    "pi": (".pi", "---\nname: graphify"),
}


def graphify_agent() -> MetaAgent:
    """Build canonical thearc configuration from the checked-in Graphify files."""
    antigravity = MetaAgent.from_project("antigravity", GRAPHIFY_ROOT, name="graphify")
    codex = MetaAgent.from_project("codex", GRAPHIFY_ROOT, name="graphify")

    return MetaAgent(
        name="graphify",
        skills=antigravity.skills,
        hooks=codex.hooks,
        context=antigravity.context,
        resources=antigravity.resources,
    )


@pytest.mark.parametrize(("implementation", "target_dir", "skill_header"), [
    (implementation, *target) for implementation, target in TARGETS.items()
])
def test_graphify_meta_configuration_installs_for_each_agent(
    tmp_path: Path,
    implementation: str,
    target_dir: str,
    skill_header: str,
) -> None:
    """Install Graphify into every supported local agent layout."""
    source_skill = Skill.from_dir(GRAPHIFY_SKILL_DIR)
    result = graphify_agent().install(
        implementation=implementation,
        replace=True,
        project_dir=tmp_path,
    )

    target_base = tmp_path / target_dir
    installed_skill = target_base / "skills" / "graphify" / "SKILL.md"
    installed_hooks = target_base / "hooks.json"
    installed_context = tmp_path / "AGENTS.md"

    assert result["skills"]
    assert result["hooks"] == [installed_hooks]
    assert result["context"] == [installed_context]
    assert result["resources"]

    assert installed_skill.is_file()
    assert installed_skill.read_text(encoding="utf-8").startswith(skill_header)
    assert "# /graphify" in installed_skill.read_text(encoding="utf-8")

    # Graphify contains a non-Markdown marker and several nested Markdown
    # references; all auxiliary skill assets must survive the conversion.
    for relative_path, content in source_skill.files.items():
        installed_file = installed_skill.parent / relative_path
        assert installed_file.read_text(encoding="utf-8") == content

    hooks = json.loads(installed_hooks.read_text(encoding="utf-8"))
    if implementation == "codex":
        assert hooks == json.loads(GRAPHIFY_HOOKS_FILE.read_text(encoding="utf-8"))
    else:
        graphify_hook = next(hook for hook in hooks.values() if hook["command"] == "graphify hook-check")
        assert graphify_hook["matcher"] == "Bash"

    assert installed_context.read_text(encoding="utf-8") == GRAPHIFY_CONTEXT_FILE.read_text(encoding="utf-8")
    assert (target_base / "rules" / "graphify.md").read_text(encoding="utf-8") == (
        GRAPHIFY_ROOT / ".agents" / "rules" / "graphify.md"
    ).read_text(encoding="utf-8")
    assert (target_base / "workflows" / "graphify.md").read_text(encoding="utf-8") == (
        GRAPHIFY_ROOT / ".agents" / "workflows" / "graphify.md"
    ).read_text(encoding="utf-8")


def test_graphify_project_import_reads_native_agent_configurations() -> None:
    """Import the checked-in Graphify layouts without hand-building models."""
    antigravity = MetaAgent.from_project("antigravity", GRAPHIFY_ROOT)
    codex = MetaAgent.from_project("codex", GRAPHIFY_ROOT)

    assert antigravity.skills["graphify"].files[".graphify_version"]
    assert {
        f"{resource.location}/{resource.path}" for resource in antigravity.resources.values()
    } == {"rules/graphify.md", "workflows/graphify.md"}

    assert len(codex.hooks) == 1
    hook = next(iter(codex.hooks.values()))
    assert hook.event == "pre_tool_use"
    assert hook.matcher == "Bash"
    assert hook.command == "graphify hook-check"
