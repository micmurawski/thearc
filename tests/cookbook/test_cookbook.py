"""Portable evidence recipes and the resource ACE recipe run without inference."""
import json
import subprocess

import pytest

from cookbook import inspect_evidence, prepare_handoff, resource_ace, snapshot_sessions
from thearc import MetaAgent
from thearc.learning.evidence.snapshot import EvidenceSnapshot
from thearc.learning.sessions.models import Event, SourceConfig
from thearc.learning.sessions.store import SessionStore
from thearc.resources import Curator, Reflector, Session


@pytest.fixture
def recipe_inputs(tmp_path):
    project = tmp_path / "project"
    skill = project / ".codex" / "skills" / "graphify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: graphify\ndescription: Test graph skill\n---\n# Query\nInspect graphify.\n"
    )
    (skill / "references").mkdir()
    (skill / "references" / "query.md").write_text("# Query\nRead the graph before editing.\n")
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.org",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture"],
                   check=True, capture_output=True)
    source = tmp_path / "source"
    source.mkdir()
    records = [
        {"type": "session_meta", "payload": {"id": "native"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
         "content": [{"type": "input_text", "text": "Private instructions"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "Please inspect graphify"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Inspected graphify"}]}},
    ]
    (source / "rollout-test.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    index = tmp_path / "sessions.sqlite"
    with SessionStore(index) as store:
        store.ingest(SourceConfig(id="fixture", harness="codex", root=source))
        session_id = store.list_sessions()[0].id
    snapshot = tmp_path / "evidence"
    snapshot_sessions.main(["--index", str(index), "--session-id", session_id, "--output", str(snapshot)])
    return project, snapshot


def test_offline_evidence_recipes(recipe_inputs, tmp_path):
    project, snapshot = recipe_inputs
    inspect_evidence.main(["--snapshot", str(snapshot), "--project", str(project),
                           "--output", str(tmp_path / "inspection")])
    prepare_handoff.main(["--snapshot", str(snapshot), "--project", str(project), "--workspace", str(project),
                          "--task", "Inspect results", "--output", str(tmp_path / "plan")])
    assert (tmp_path / "inspection/inspection.json").is_file()


def test_resource_recipe_commits_without_mutating_input(recipe_inputs, tmp_path, monkeypatch):
    project, snapshot = recipe_inputs
    agent = MetaAgent.from_project("codex", project)
    before = agent.model_dump()
    history = Session.from_events([Event.model_validate(e) for e in EvidenceSnapshot.load(snapshot).iter_events()])
    def reflect(*args, **kwargs):
        return Reflector(**kwargs, runner=lambda *a: {"status": "completed", "final_response":
            '{"summary":"No supported ratings", "findings":[], "limitations":["Fixture"]}'})
    def curate(*args, **kwargs):
        return Curator(**kwargs, runner=lambda *a: {"status": "completed", "final_response":
            '{"summary":"Retain configuration", "changes":[]}'})
    monkeypatch.setattr(resource_ace, "Reflector", reflect)
    monkeypatch.setattr(resource_ace, "Curator", curate)
    result = resource_ace.adapt(agent, history, implementation="custom", model="fixture", output=tmp_path / "ace")
    assert result.agent.without_ranks() == agent.without_ranks()
    assert agent.model_dump() == before
    assert (tmp_path / "ace/curation/HEAD.json").is_file()
