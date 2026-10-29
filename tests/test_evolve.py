"""/evolve end to end, without model calls: install the evolve skill into a project for a harness, run a round with
scripted runtimes, amend the proposal, apply it, and pick up a hand edit on the next round."""

import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from thearc import evolve
from thearc.curation import Curator
from thearc.reflection import Reflector
from thearc.resources import MetaAgent, install

from .test_curation import ScriptedCurator, edit
from .test_reflection import GOOD, SCHEMA, Scripted, answer


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(agent, tmp_path, monkeypatch):
    """A git project with a hand-made Claude configuration (the ``agent`` fixture installed), and a Claude session
    store holding its sessions."""
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    install(agent, "claude", root)
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Project")
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    store = home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
    store.mkdir(parents=True)
    now = "2099-01-01T10:00:00Z"
    for native, text in (("s1", "Fix the retry bug"), ("s2", "Add a test"), ("s3", "Refactor retries")):
        (store / f"{native}.jsonl").write_text(json.dumps(
            {"type": "user", "sessionId": native, "cwd": str(root.resolve()), "timestamp": now,
             "message": {"role": "user", "content": text}}) + "\n")
    evolved = {"type": "assistant", "sessionId": "s4", "cwd": str(root.resolve()), "timestamp": now,
               "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "thearc evolve --json"}}]}}
    (store / "s4.jsonl").write_text(json.dumps(evolved) + "\n")  # An /evolve run: never evidence.
    return root


def scripted_recipe(edits, seen):
    """A recipe like the default one, with scripted reflector and curator."""

    def propose(evolution):
        sessions = evolution.sessions(since=timedelta(days=36500))
        seen["sessions"] = sorted(s.metadata.native_id for s in sessions)
        if not edits:
            return evolution.nothing("nothing to change")
        candidate = evolution.candidate()
        reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", candidate, SCHEMA)
        candidate.add_reflection(reflection)

        def script(root, tool):
            for old, new, reason in edits:
                edit(root, old, new)
                assert not tool("commit_change", message=reason)["error"]
            tool("finish", summary="Done.")

        Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [reflection], candidate)
        return candidate.reload()

    return propose


WORKSPACE = ".thearc/workspace"


def test_install_adopts_the_harness_configuration_beside_the_evolve_skill(project):
    setup = evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # A repository of its own…
    assert git(project, "ls-files", "--stage", WORKSPACE).startswith("160000")  # …added as a submodule.
    assert "path = .thearc/workspace" in (project / ".gitmodules").read_text()
    assert not git(project, "for-each-ref", "refs/heads/thearc")  # No workspace branches in the project.
    assert setup.agent.name == "app" and set(setup.agent.skills) == {"retry"}
    assert setup.agent.ignore == ["skill:evolve"] and not setup.agent.frozen  # Never part of what evolves.
    skill = project / ".claude/skills/evolve"
    assert 'RUNTIME = "claude"' in (skill / "evolve.py").read_text() and (skill / "prompts/curate.md").is_file()
    assert 'HARNESS = "claude"' in (skill / "evolve.py").read_text()  # Only Claude's sessions are read.
    assert "`thearc evolve diff`" in (skill / "SKILL.md").read_text()
    record = json.loads((project / ".thearc/installed.json").read_text())["agents"]
    assert ".claude/skills/evolve/SKILL.md" in record["thearc-evolve"]["harnesses"]["claude"]["files"]
    ignored = set((project / ".gitignore").read_text().splitlines())
    assert "/.thearc/cache/" in ignored and "/.thearc/workspace/" not in ignored
    assert git(project, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # The project's checkout is untouched.
    status = evolve.status(project)
    assert status["installed"] == "1.0.0" and status["runtime"] == "claude" and status["harness"] == "claude"


def test_install_needs_git_and_no_evolve_skill_of_the_projects_own(agent, tmp_path):
    with pytest.raises(ValueError, match="git"):
        evolve.install_evolve_skill(tmp_path, "claude")
    git(tmp_path, "init", "-q")
    mine = agent.model_copy(update={"skills": {"evolve": agent.skills["retry"].model_copy(update={"name": "evolve"})}})
    install(mine, "claude", tmp_path)
    with pytest.raises(ValueError, match="named 'evolve'"):
        evolve.install_evolve_skill(tmp_path, "claude")


def test_a_round_proposes_amends_and_applies(project):
    evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe_text = recipe.read_text()
    seen = {}
    proposal = evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries are enough")], seen))
    assert seen["sessions"] == ["s1", "s2", "s3"]  # The /evolve run is left out.
    assert proposal.version == "1.0.1" and proposal.base == "1.0.0" and "max 2" in proposal.diff
    (change,) = proposal.changes
    assert change.target == "skill:retry#Retry > Limits" and change.reason == "Two tries are enough"
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "proposal/1.0.1"
    assert "max 3" in (project / ".claude/skills/retry/SKILL.md").read_text()  # Not installed yet.
    assert evolve.status(project)["proposal"] == "1.0.1"
    with pytest.raises(ValueError, match="open"):
        evolve.run(project, recipe=scripted_recipe([], {}))

    edit(workspace, "max 2", "max 2, then report")  # The user asks the agent for an amendment.
    assert evolve.amend(project, "Say what happens after the last try")
    amended = evolve.diff(project)
    assert ("skill:retry#Retry > Limits", "user") in {(c.target, c.author) for c in amended.changes}
    assert "max 2, then report" in amended.diff

    applied = evolve.apply(project)
    assert applied.version == "1.0.1" and applied.harnesses == ("claude",)
    assert "max 2, then report" in (project / ".claude/skills/retry/SKILL.md").read_text()
    assert recipe.read_text() == recipe_text  # The evolve skill is not the configuration's: left alone.
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    branches = git(workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    assert "release/1.0.1" in branches and not any("proposal" in b for b in branches)
    assert git(project, "status", "--porcelain", WORKSPACE).endswith("M .thearc/workspace")  # The submodule moved.
    assert MetaAgent.from_workspace(workspace).version == "1.0.1"

    installed = project / ".claude/skills/retry/SKILL.md"  # A hand edit before the next round…
    installed.write_text(installed.read_text().replace("ex1", "ex1, by hand"))
    nothing = evolve.run(project, recipe=scripted_recipe([], seen))
    assert nothing.version is None and nothing.reason == "nothing to change"
    assert nothing.drift == ("modified skill:retry#Retry > Examples",)  # …is committed, not overwritten.
    assert "ex1, by hand" in MetaAgent.from_workspace(workspace).skills["retry"].instructions


def test_discard_drops_the_proposal_and_its_edits(project):
    evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries")], {}))
    edit(workspace, "max 2", "max 9")
    assert evolve.discard(project) == "1.0.1"
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "max 3" in (workspace / "skills/retry/SKILL.md").read_text()


def test_ignoring_leaves_the_installed_skill_alone(project):
    evolve.install_evolve_skill(project, "claude")
    agent = evolve.ignore(project, ["skill:retry"])
    assert "retry" not in agent.skills and (project / ".claude/skills/retry/SKILL.md").is_file()
    assert evolve.status(project)["ignored"] == ["skill:evolve", "skill:retry"]
    back = evolve.ignore(project, ["skill:retry"], remove=True)
    assert "retry" in back.skills  # Adopted again from the installation.


def test_the_default_recipe_is_the_installed_one_and_waits_for_enough_sessions(project, tmp_path):
    evolve.install_evolve_skill(project, "claude")
    for transcript in (tmp_path / "claude-home/projects").rglob("*.jsonl"):
        transcript.unlink()
    proposal = evolve.run(project)  # .claude/skills/evolve/evolve.py, its prompts read from next to it.
    assert proposal.version is None and proposal.reason == "0 session(s) in the last 7 days; 3 needed"
    assert not (project / ".claude/skills/evolve/__pycache__").exists()
    page = Path(proposal.progress).read_text()  # The round's flow, as it ran: it stopped at "too few".
    assert 'class="chip success">success' in page and "TooFew" in page and "0 claude session(s)" in page


def test_the_default_recipe_shows_its_progress_and_proposes(project, monkeypatch):
    import dataclasses

    import thearc.curation
    import thearc.reflection

    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"}]}

    failed: list[str] = []

    class ScriptedReflector:  # The recipe's Reflector(RUNTIME), without a model; the first reflection fails.
        def __init__(self, harness):
            self.harness = harness

        def handoff(self, prompt, session, agent, schema, *, provenance=None):
            if not failed:
                failed.append(session.id)
                raise thearc.reflection.ReflectionError("no_submission", "ended without a submission")
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, evidence={"session": session.id, "provenance": provenance})

    def curator(harness):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")
        return Curator(runtime=ScriptedCurator(script))

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    evolve.install_evolve_skill(project, "claude")
    proposal = evolve.run(project)
    assert proposal.version == "1.0.1" and proposal.sessions == 3 and "max 2" in proposal.diff
    page = Path(proposal.progress).read_text()
    assert page.count('class="chip success">success') == 3  # FindSessions, Reflect, Curate.
    assert "3 claude session(s)" in page and "1 rating(s) (1 harmful)" in page and "Two tries are enough" in page
    assert f"{failed[0]}: no reflection (ended without a submission); going on without it" in page
    assert (project / ".thearc/cache/progress.html").read_text() == page


def test_a_project_evolves_one_harness(project):
    evolve.install_evolve_skill(project, "claude")
    with pytest.raises(ValueError, match="already evolves its claude configuration"):
        evolve.install_evolve_skill(project, "codex")
    assert evolve.install_evolve_skill(project, "claude", runtime="codex").runtime == "codex"  # Reinstalled.
    assert 'RUNTIME = "codex"' in (project / ".claude/skills/evolve/evolve.py").read_text()


def test_the_command_line(project, tmp_path):
    from click.testing import CliRunner

    from thearc.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "install", "self-evolve", "--project", str(project), "--harness", "claude"])
    assert result.exit_code == 0, result.output
    assert "app 1.0.0 is set up for evolution in claude" in result.output
    for transcript in (tmp_path / "claude-home/projects").rglob("*.jsonl"):
        transcript.unlink()
    result = runner.invoke(main, ["evolve", "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["version"] is None
    result = runner.invoke(main, ["evolve", "--project", str(project), "status", "--json"])
    assert json.loads(result.output)["runtime"] == "claude"
    result = runner.invoke(main, ["evolve", "--project", str(project), "apply"])
    assert result.exit_code == 1 and "no proposal is open" in result.output
    result = runner.invoke(main, ["skills", "uninstall", "self-evolve", "--project", str(project)])
    assert result.exit_code == 0 and not (project / ".claude/skills/evolve").exists()
    assert (project / ".claude/skills/retry/SKILL.md").is_file()


def test_a_teammate_gets_the_workspace_with_the_project(project, tmp_path, monkeypatch):
    for key, value in (("COUNT", "1"), ("KEY_0", "protocol.file.allow"), ("VALUE_0", "always")):
        monkeypatch.setenv(f"GIT_CONFIG_{key}", value)  # Local paths as remotes, for the test.
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    evolve.install_evolve_skill(project, "claude", remote=str(shared))
    assert git(shared, "rev-parse", "main")  # Pushed at install.
    assert f"url = {shared}" in (project / ".gitmodules").read_text()
    git(project, "add", "-A")
    git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Set up evolution")
    origin = tmp_path / "project.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(project, "push", "-q", str(origin), "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    agent = evolve.init_from_remote(clone)
    assert agent.version == "1.0.0" and set(agent.skills) == {"retry"}
    assert git(clone / WORKSPACE, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert evolve.status(clone)["remote"] == str(shared)


def test_apply_pushes_the_workspace_when_asked(project, tmp_path, monkeypatch):
    evolve.install_evolve_skill(project, "claude")
    evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries")], {}))
    with pytest.raises(ValueError, match="no remote"):
        evolve.apply(project, push=True)
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    evolve.setup.set_remote(project, str(shared))
    assert f"url = {shared}" in (project / ".gitmodules").read_text()
    applied = evolve.apply(project, push=True)
    assert applied.pushed == str(shared)
    assert git(shared, "rev-parse", "main") == git(project / WORKSPACE, "rev-parse", "main")
    assert git(shared, "rev-parse", "--verify", "release/1.0.1")
