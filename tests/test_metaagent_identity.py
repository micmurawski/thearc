import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from thearc import MetaAgent, Skill
from thearc.agents import get_installer


def test_name_is_required_for_constructor_json_and_workspace(tmp_path):
    assert "name" in MetaAgent.model_json_schema()["required"]
    with pytest.raises(ValidationError, match="name"):
        MetaAgent()
    with pytest.raises(ValidationError, match="name"):
        MetaAgent.model_validate_json('{"version": "1.0.0"}')
    workspace = MetaAgent(name="graphify").to_workspace(tmp_path / "workspace")
    manifest = workspace / "agent.json"
    data = json.loads(manifest.read_text())
    del data["agent"]["name"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="name"):
        MetaAgent.from_workspace(workspace)


@pytest.mark.parametrize("implementation", ["codex", "claude", "antigravity", "pi"])
def test_identity_survives_install_import_and_workspace(tmp_path, implementation):
    agent = MetaAgent(
        name="graphify-config", version="1.2.3",
        skills=[Skill(name="graphify", instructions="Use the graph.",
                      files={".graphify_version": "0.9.68"})],
    )
    project = tmp_path / implementation
    result = agent.install(implementation, path=project)
    metadata = result["metadata"][0]
    assert json.loads(metadata.read_text()) == {
        "schema_version": 1, "name": "graphify-config", "version": "1.2.3",
    }
    marker = metadata.parent / ".graphify-config_version"
    assert marker.read_text() == "1.2.3\n"
    assert marker in result["metadata"]
    imported = MetaAgent.from_project(implementation, project)
    assert (imported.name, imported.version) == ("graphify-config", "1.2.3")
    assert imported.skills["graphify"].files[".graphify_version"] == "0.9.68"
    workspace = imported.to_workspace(tmp_path / "workspace")
    assert (workspace / ".graphify-config_version").read_text() == "1.2.3\n"
    assert MetaAgent.from_workspace(workspace) == imported
    assert MetaAgent.model_validate_json(imported.model_dump_json()) == imported
    assert imported.without_ranks().version == "1.2.3"


def test_old_unversioned_projects_and_explicit_overrides(tmp_path):
    assert MetaAgent(name="agent").version is None
    assert MetaAgent.model_validate({"name": "legacy"}).version is None
    imported = MetaAgent.from_project("codex", tmp_path)
    assert imported.name == "codex-project"
    assert imported.version is None
    MetaAgent(name="installed", version="2026.09").install("codex", path=tmp_path)
    override = MetaAgent.from_project("codex", tmp_path, name="fork", version="2.0.0-rc.1")
    assert (override.name, override.version) == ("fork", "2.0.0-rc.1")
    assert MetaAgent.from_project("codex", tmp_path).name == "installed"


@pytest.mark.parametrize("field", ["name", "version"])
@pytest.mark.parametrize("invalid", ["", "   ", "one\ntwo", "one\x00two"])
def test_invalid_identity_rejected_on_creation_and_assignment(field, invalid):
    with pytest.raises(ValidationError):
        MetaAgent(**{"name": "agent", field: invalid})
    agent = MetaAgent(name="agent")
    with pytest.raises(ValidationError):
        setattr(agent, field, invalid)


def test_versions_are_explicit_labels_not_automatic_content_counters():
    agent = MetaAgent(name=" example ", version=" release-2026.09 ")
    assert (agent.name, agent.version) == ("example", "release-2026.09")
    agent.skills["new"] = Skill(name="new", instructions="changed")
    assert agent.version == "release-2026.09"
    agent.version = None
    assert agent.version is None


def test_merge_does_not_claim_incoming_release(tmp_path):
    MetaAgent(name="first", version="1.0.0").install("codex", path=tmp_path)
    MetaAgent(name="merged", version="2.0.0").install("codex", path=tmp_path, replace=False)
    merged = MetaAgent.from_project("codex", tmp_path)
    assert merged.name == "merged"
    assert merged.version is None
    assert not (tmp_path / ".codex/.first_version").exists()
    assert not (tmp_path / ".codex/.merged_version").exists()


def test_graphify_named_marker_and_version_updates(tmp_path):
    agent = MetaAgent(name="graphify", version="0.9.68")
    agent.install("codex", path=tmp_path)
    marker = tmp_path / ".codex/.graphify_version"
    assert marker.read_text().strip() == "0.9.68"
    agent.version = "0.9.69"
    agent.install("codex", path=tmp_path)
    assert marker.read_text().strip() == "0.9.69"
    unrelated = marker.parent / ".another_version"
    unrelated.write_text("5")
    agent.name = "graphify-fork"
    agent.install("codex", path=tmp_path)
    assert not marker.exists()
    assert unrelated.read_text() == "5"
    assert (marker.parent / ".graphify-fork_version").read_text().strip() == "0.9.69"
    agent.version = None
    agent.install("codex", path=tmp_path)
    assert not (marker.parent / ".graphify-fork_version").exists()


def test_marker_only_import_and_ambiguous_names(tmp_path):
    base = tmp_path / ".codex"
    base.mkdir()
    (base / ".graphify_version").write_text("0.9.68")
    imported = MetaAgent.from_project("codex", tmp_path)
    assert (imported.name, imported.version) == ("graphify", "0.9.68")
    (base / ".other_version").write_text("2")
    with pytest.raises(ValueError, match="Multiple"):
        MetaAgent.from_project("codex", tmp_path)
    explicit = MetaAgent.from_project("codex", tmp_path, name="graphify")
    assert explicit.version == "0.9.68"


@pytest.mark.parametrize("name", ["../outside", "a/b", "a\\b", ".", "..", "c:drive"])
def test_unsafe_names_rejected(name):
    with pytest.raises(ValueError, match="filename"):
        MetaAgent(name=name, version="1")


def test_version_marker_symlink_rejected_before_writes(tmp_path):
    base = tmp_path / ".codex"
    base.mkdir()
    target = tmp_path / "original"
    target.write_text("unchanged")
    (base / ".graphify_version").symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        MetaAgent(name="graphify", version="1", skills=[Skill(name="new")]).install("codex", path=tmp_path)
    assert target.read_text() == "unchanged"
    assert not (base / "skills").exists()


def test_inconsistent_marker_is_rejected_in_installation_and_workspace(tmp_path):
    agent = MetaAgent(name="graphify", version="1")
    agent.install("codex", path=tmp_path)
    (tmp_path / ".codex/.graphify_version").write_text("2")
    with pytest.raises(ValueError, match="disagrees"):
        MetaAgent.from_project("codex", tmp_path)
    workspace = agent.to_workspace(tmp_path / "workspace")
    (workspace / ".graphify_version").write_text("2")
    with pytest.raises(ValueError, match="disagrees"):
        MetaAgent.from_workspace(workspace)


def test_legacy_sidecars_and_workspaces_without_marker_still_load(tmp_path):
    agent = MetaAgent(name="graphify", version="1")
    agent.install("codex", path=tmp_path)
    (tmp_path / ".codex/.graphify_version").unlink()
    assert MetaAgent.from_project("codex", tmp_path).version == "1"
    workspace = agent.to_workspace(tmp_path / "workspace")
    (workspace / ".graphify_version").unlink()
    assert MetaAgent.from_workspace(workspace) == agent


def test_malformed_sidecar_is_not_silently_ignored(tmp_path):
    base = get_installer("codex").get_local_paths(tmp_path)["base"]
    base.mkdir()
    marker = base / ".thearc-agent.json"
    marker.write_text('{"schema_version": 999, "name": "invalid", "version": "1"}')
    with pytest.raises(ValueError, match="identity"):
        MetaAgent.from_project("codex", tmp_path)


def test_symlink_sidecar_rejected_before_installing_resources(tmp_path):
    base = get_installer("codex").get_local_paths(tmp_path)["base"]
    base.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("untouched")
    (base / ".thearc-agent.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        MetaAgent(name="agent", skills=[Skill(name="new")]).install("codex", path=tmp_path)
    assert outside.read_text() == "untouched"
    assert not (base / "skills").exists()


def test_graphify_marker_is_preserved_without_inventing_metaagent_version():
    project = Path(__file__).parent / "fastapi"
    marker = project / ".codex/skills/graphify/.graphify_version"
    if not marker.is_file():
        pytest.skip("Graphify fixture is not initialized")
    agent = MetaAgent.from_project("codex", project, name="graphify")
    assert agent.skills["graphify"].files[".graphify_version"] == marker.read_text()
    assert agent.version is None
