import json
from pathlib import Path

import pytest

from thearc import MCP, AgentResource, ContextDocument, Hook, MetaAgent, Skill
from thearc.models import Ranks


def config():
    return MetaAgent(
        name="graphify-test",
        skills=[Skill(name="graphify", description="Graph tool", instructions="# Guide\r\n\r\nαβ\n",
                      ranks=Ranks(helpful=2), metadata={"custom": ["retained"]},
                      files={"references/query.md": "# Query\nDetails\n", "scripts/query.py": "print('query')\n"})],
        hooks=[Hook(name="guard", script="exit 1", extra={"custom": True})],
        mcps=[MCP(name="local", command="never-execute", env={"TOKEN": "local-secret"})],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\nContent", description="context")],
        resources=[AgentResource(location=kind, path="nested/item.md", content=f"# {kind}\n")
                   for kind in ["rules", "commands", "workflows"]],
    )


def test_lossless_roundtrip_and_edit_nested_reference(tmp_path):
    original = config()
    path = original.to_workspace(tmp_path / "candidate")
    assert MetaAgent.from_workspace(path) == original
    reference = path / "skills/graphify/files/references/query.md"
    reference.write_text("# Query\nImproved guidance\n")
    restored = MetaAgent.from_workspace(path)
    assert restored.skills["graphify"].files["references/query.md"] == "# Query\nImproved guidance\n"
    assert original.skills["graphify"].files["references/query.md"] != restored.skills["graphify"].files[
        "references/query.md"]
    with pytest.raises(FileExistsError):
        original.to_workspace(path)


def test_manifest_add_remove_resources(tmp_path):
    path = config().to_workspace(tmp_path / "candidate")
    manifest_path = path / "agent.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["agent"]["context"]["NEW.md"] = {"filename": "NEW.md", "content": {"file": "context/NEW.md"}}
    (path / "context/NEW.md").write_text("New")
    del manifest["agent"]["skills"]["graphify"]["files"]["references/query.md"]
    (path / "skills/graphify/files/references/query.md").unlink()
    manifest_path.write_text(json.dumps(manifest))
    agent = MetaAgent.from_workspace(path)
    assert agent.context["NEW.md"].content == "New"
    assert "references/query.md" not in agent.skills["graphify"].files


@pytest.mark.parametrize("bad", ["../escape", "/absolute", "a/../../escape", "a\\escape", "a//file", "a/./file"])
def test_export_rejects_unsafe_paths_before_writes(tmp_path, bad):
    original = config()
    original.skills["graphify"].files[bad] = "unsafe"
    with pytest.raises(ValueError, match="Unsafe"):
        original.to_workspace(tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("problem", ["orphan", "missing", "symlink", "escape", "duplicate", "version", "inline"])
def test_invalid_import(tmp_path, problem):
    path = config().to_workspace(tmp_path / "candidate")
    manifest_path = path / "agent.json"
    manifest = json.loads(manifest_path.read_text())
    if problem == "orphan":
        (path / "extra.md").write_text("Not in manifest")
    elif problem == "missing":
        (path / "context/AGENTS.md").unlink()
    elif problem == "symlink":
        (path / "escape").symlink_to(tmp_path, target_is_directory=True)
    elif problem == "version":
        manifest["schema_version"] = 999
    else:
        manifest["agent"]["context"]["AGENTS.md"]["content"] = {
            "escape": {"file": "../outside.md"},
            "duplicate": {"file": "skills/graphify/instructions.md"},
            "inline": "not a reference",
        }[problem]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        MetaAgent.from_workspace(path)


def test_limits_and_duplicate_json_keys(tmp_path):
    path = config().to_workspace(tmp_path / "candidate")
    with pytest.raises(ValueError, match="byte limit"):
        MetaAgent.from_workspace(path, max_bytes=10)
    with pytest.raises(ValueError, match="entry limit"):
        MetaAgent.from_workspace(path, max_files=2)
    (path / "agent.json").write_text('{"schema_version": 1, "schema_version": 1, "agent": {}}')
    with pytest.raises(ValueError, match="Duplicate manifest key"):
        MetaAgent.from_workspace(path)


def test_noop_empty_configuration(tmp_path):
    empty = MetaAgent(name="agent")
    assert MetaAgent.from_workspace(empty.to_workspace(tmp_path / "empty")) == empty


def test_graphify_fixture_roundtrip_without_modifying_source(tmp_path):
    project = Path(__file__).parent / "fastapi"
    skill_path = project / ".codex/skills/graphify"
    if not (skill_path / "SKILL.md").is_file():
        pytest.skip("Graphify submodule fixture is not initialized")
    original = MetaAgent.from_project("codex", project)
    assert original.skills["graphify"].files["references/query.md"]
    restored = MetaAgent.from_workspace(original.to_workspace(tmp_path / "graphify"))
    assert restored == original
