"""Skill identity, historical paths, evidence provenance, and matcher composition."""

import json
from pathlib import Path

import pytest

from thearc.resources import MetaAgent, Skill
from thearc.sessions import Session
from thearc.sessions.scanner import AllOf, PatternScanner, ShellCommand, SkillUsage

from .conftest import event


@pytest.fixture
def config():
    return MetaAgent(name="test", skills=[Skill(name="graphify", instructions="Query first.", files={
        "references/query.md": "Query help", "scripts/check.sh": "echo check",
    }), Skill(name="other", instructions="Other skill.")])


def call(n=0, *, tool="Read", args=None, cwd="/old/project"):
    return event(n, kind="tool_call", tool_name=tool, arguments=args, cwd=cwd, call_id=f"c{n}")


def result(n=1, *, call_id="c0", status="success"):
    return event(n, kind="tool_result", call_id=call_id, status=status)


@pytest.mark.parametrize("root", ["skills", ".claude/skills", ".codex/skills", ".agents/skills",
                                 ".pi/skills", ".pi/agent/skills", ".opencode/skills"])
def test_portable_selector_and_agent_across_layouts(config, root):
    observed = f"/old/project/{root}/graphify/SKILL.md" if root != "skills" else "skills/graphify/SKILL.md"
    events = [call(args={"file_path": observed}), result()]
    for matcher in (SkillUsage(agent=config), SkillUsage(path="skills/graphify"),
                    SkillUsage(agent=config, path="skills/graphify/SKILL.md")):
        (match,) = Session.from_events(events).match(matcher)
        assert match.context["skill"] == "graphify" and match.context["usage"] == "read"
        assert match.context["canonical_path"] == "skills/graphify/SKILL.md"
        assert match.context["observed_path"] == observed
        assert match.context["status"] == "success" and match.event_ids == ["e0", "e1"]


def test_agent_only_selects_its_defined_resources(config):
    events = [call(0, args={"path": ".agents/skills/graphify/references/query.md"}),
              call(1, args={"path": ".agents/skills/unknown/SKILL.md"}),
              call(2, args={"path": ".agents/skills/graphify/unknown.md"}),
              call(3, args={"path": ".agents/skills/other/SKILL.md"})]
    assert [m.start_index for m in SkillUsage(agent=config).find_matches(events)] == [0, 3]
    assert len(SkillUsage(agent=config, path="skills/graphify").find_matches(events)) == 1
    assert SkillUsage(agent=MetaAgent(name="empty")).find_matches(events) == []
    assert len(SkillUsage(path="skills/graphify").find_matches(events)) == 2


def test_path_narrows_to_file_or_subtree(config):
    events = [call(0, args={"path": ".claude/skills/graphify/SKILL.md"}),
              call(1, args={"path": ".claude/skills/graphify/references/query.md"}),
              call(2, args={"path": ".claude/skills/graphify/references-elsewhere/query.md"})]
    (match,) = SkillUsage(path="skills/graphify/references").find_matches(events)
    assert match.start_index == 1
    (narrowed,) = SkillUsage(agent=config, path="skills/graphify/references/query.md").find_matches(events)
    assert narrowed.start_index == 1 and narrowed.context["agent_revision"] == config.revision


def test_exact_paths_use_recorded_workdir_not_local_filesystem(config):
    events = [call(0, args={"path": ".claude/skills/graphify/SKILL.md", "workdir": "/gone/worktree"}),
              call(1, args={"path": ".codex/skills/graphify/SKILL.md", "workdir": "/gone/worktree"}),
              call(2, args={"path": ".claude/skills/graphify/SKILL.md"})]
    matcher = SkillUsage(agent=config, path=Path("/gone/worktree/.claude/skills/graphify/SKILL.md"))
    (match,) = matcher.find_matches(events)
    assert match.start_index == 0 and match.context["basis"] == "exact_path"
    assert match.context["resolved_path"] == str(matcher.path)
    relative = SkillUsage(path=".claude/skills/graphify/SKILL.md")
    assert [m.start_index for m in relative.find_matches(events)] == [0, 2]


def test_explicit_custom_skill_directory_and_file():
    events = [call(args={"path": "/historic/custom-skill/SKILL.md"})]
    for path in ("/historic/custom-skill", "/historic/custom-skill/SKILL.md"):
        (match,) = SkillUsage(path=path).find_matches(events)
        assert match.context["skill"] == "custom-skill"
    assert not SkillUsage(path="skills/custom-skill").find_matches(events)


def test_native_invocation_and_user_request_remain_distinct(config):
    events = [event(0, role="user", text="/graphify explain routing"),
              call(1, tool="Skill", args=json.dumps({"skill": "graphify"})), result(2, call_id="c1"),
              event(3, text="I used graphify"), event(4, role="user", text="Maybe /graphify later")]
    matches = SkillUsage(agent=config).find_matches(events)
    assert [m.context["usage"] for m in matches] == ["requested", "invoked"]
    assert matches[0].context["status"] == "unknown"
    assert matches[1].context["status"] == "success" and matches[1].context["basis"] == "skill_name"
    assert not SkillUsage(path=".claude/skills/graphify/SKILL.md").find_matches(events)
    assert not SkillUsage(path="skills/graphify/references/query.md").find_matches(events)


@pytest.mark.parametrize("command,usage", [
    ("cat .agents/skills/graphify/SKILL.md", "read"),
    ("head -n 20 .codex/skills/graphify/SKILL.md", "read"),
    ("sed -n '1,80p' .claude/skills/graphify/SKILL.md", "read"),
    ("bash .agents/skills/graphify/scripts/check.sh", "executed"),
    ("./.agents/skills/graphify/scripts/check.sh", "executed"),
])
def test_shell_skill_files(config, command, usage):
    (match,) = SkillUsage(agent=config).find_matches([call(tool="bash", args={"command": command}), result()])
    assert match.context["usage"] == usage and match.context["command_index"] == 0
    assert match.context["status"] == "success" and match.context["outcome_scope"] == "shell_script"


def test_shell_cd_and_read_relative_paths_within_the_skill(config):
    commands = "cd /gone/.agents/skills/graphify && cat SKILL.md; cat references/query.md"
    matches = SkillUsage(agent=config).find_matches([call(tool="bash", args={"command": commands})])
    assert [m.context["canonical_path"] for m in matches] == [
        "skills/graphify/SKILL.md", "skills/graphify/references/query.md"]


@pytest.mark.parametrize("command", [
    'echo "cat .agents/skills/graphify/SKILL.md"',
    "ls .agents/skills/graphify/SKILL.md",
    "graphify query route",  # CLI usage alone does not prove a skill was loaded.
    "cat $SKILL/SKILL.md",
    "cat $(find . -name SKILL.md)",
    "sed -i 's/a/b/' .agents/skills/graphify/SKILL.md",
    'cat <<EOF\ncat .agents/skills/graphify/SKILL.md\nEOF',
    "cat unrelated.md > .agents/skills/graphify/SKILL.md",
])
def test_shell_mentions_dynamic_paths_and_writes_are_not_reads(config, command):
    assert not SkillUsage(agent=config).find_matches([call(tool="bash", args={"command": command})])


def test_wrapped_reads_compose_on_the_same_command(config):
    code = '''tools.exec_command({cmd: "cat .agents/skills/graphify/SKILL.md"});
tools.exec_command({cmd: "pytest"});'''
    events = [event(0, role="user", text="Test"), call(1, tool="functions.exec", args=code),
              result(2, call_id="c1")]
    matcher = SkillUsage(agent=config)
    (match,) = Session.from_events(events).match(matcher)
    assert match.context["wrapped"] and match.context["status"] == "unknown"
    assert match.context["wrapper_status"] == "success"
    assert AllOf(matcher, ShellCommand(startswith="cat"), scope="command").find_matches(events)
    assert not AllOf(matcher, ShellCommand(startswith="pytest"), scope="command").find_matches(events)
    assert Session.from_events(events).match(matcher & ShellCommand(startswith="pytest"))
    assert PatternScanner(patterns=[matcher]).scan_events(events)[0].context == match.context


def test_failures_missing_results_and_other_runs_do_not_become_success(config):
    events = [call(args={"path": ".agents/skills/graphify/SKILL.md"}), result(status="failure"),
              result(2).model_copy(update={"run_id": "other"})]
    (match,) = SkillUsage(agent=config).find_matches(events)
    assert match.context["status"] == "failure" and match.event_ids == ["e0", "e1"]
    assert SkillUsage(agent=config).find_matches(events[:1])[0].context["status"] == "unknown"


@pytest.mark.parametrize("options", [{}, {"path": ""}, {"path": "skills"}, {"path": "."}, {"path": 2}])
def test_invalid_selectors(options):
    with pytest.raises(ValueError):
        SkillUsage(**options)


def test_unknown_agent_skill_is_rejected(config):
    with pytest.raises(ValueError, match="no skill"):
        SkillUsage(agent=config, path="skills/missing")
    with pytest.raises(TypeError, match="MetaAgent"):
        SkillUsage(agent="graphify")
