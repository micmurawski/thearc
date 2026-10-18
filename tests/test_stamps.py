import json

from thearc.resources import ContextDocument, Hook, MetaAgent, Skill, install
from thearc.resources.markers import stamp_file, stamp_markdown, unstamp_file, unstamp_markdown

USER_CLAUDE = "# Project\n\nUse uv.\n\n## graphify\n\nOld instructions.\n"


def graphify(version: str | None, **update) -> MetaAgent:
    return MetaAgent(
        name="graphify", version=version, owns_hooks=["graphify *"],
        context=[ContextDocument(filename="CLAUDE.md", content="## graphify\n\nQuery the graph first.\n")],
        skills=[Skill(name="graphify", description="Build graphs", instructions="# /graphify\n\nRun it.\n",
                      files={"references/query.md": "# Query\n\nUse query.\n",
                             "scripts/run.sh": "#!/bin/sh\necho hi\n"})],
        hooks=[Hook(name="guard", event="pre_tool_use", matcher="Read", command="graphify hook-guard read")],
    ).model_copy(update=update)


def test_installed_files_carry_the_version(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(USER_CLAUDE)
    install(graphify("0.9.69"), "claude", tmp_path)

    assert (tmp_path / "CLAUDE.md").read_text() == (
        "# Project\n\nUse uv.\n\n## graphify <!-- graphify 0.9.69 -->\n\nQuery the graph first.\n")
    skill = tmp_path / ".claude/skills/graphify"
    assert (skill / "SKILL.md").read_text() == (
        "---\nname: graphify\ndescription: Build graphs\nversion: 0.9.69\n---\n"
        "# /graphify <!-- graphify 0.9.69 -->\n\nRun it.\n")
    assert (skill / "references/query.md").read_text() == "# Query <!-- graphify 0.9.69 -->\n\nUse query.\n"
    assert (skill / "scripts/run.sh").read_text() == "#!/bin/sh\necho hi  # graphify 0.9.69\n"
    hooks = json.loads((tmp_path / ".claude/settings.json").read_text())["hooks"]
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == "graphify hook-guard read"  # Hook commands: never.


def test_reinstalling_a_new_version_restamps_without_duplicates(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(USER_CLAUDE)
    install(graphify("0.9.68"), "claude", tmp_path)
    install(graphify("0.9.69"), "claude", tmp_path)

    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.count("## graphify") == 1 and "0.9.68" not in text and "<!-- graphify 0.9.69 -->" in text


def test_importing_removes_the_stamps(tmp_path):
    agent = graphify("0.9.69")
    for harness in ("claude", "codex", "pi"):
        install(agent, harness, tmp_path / harness)
        imported = MetaAgent.from_installation(harness, tmp_path / harness)
        assert imported.skills == agent.skills
        assert imported.context == agent.context
        assert "0.9.69" not in json.dumps(imported.model_dump(mode="json", exclude={"version"}))


def test_a_skill_keeps_its_own_version(tmp_path):
    agent = graphify("0.9.69")
    agent.skills["graphify"].metadata = {"version": "2.0"}
    install(agent, "pi", tmp_path)
    assert "\nversion: '2.0'\n" in (tmp_path / ".pi/skills/graphify/SKILL.md").read_text()
    assert MetaAgent.from_installation("pi", tmp_path).skills["graphify"].metadata == {"version": "2.0"}


def test_hooks_are_not_stamped(tmp_path):
    install(graphify("0.9.69"), "codex", tmp_path)
    hooks = json.loads((tmp_path / ".codex/hooks.json").read_text())["hooks"]
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == "graphify hook-guard read"


def test_an_agent_without_a_version_is_not_stamped(tmp_path):
    install(graphify(None), "claude", tmp_path)
    assert "<!--" not in (tmp_path / ".claude/skills/graphify/SKILL.md").read_text()
    assert "version" not in (tmp_path / ".claude/skills/graphify/SKILL.md").read_text()


def test_stamp_placement():
    assert stamp_markdown("Just text.\n", "a 1") == "<!-- a 1 -->\nJust text.\n"
    assert stamp_markdown("---\nx: 1\n---\nText.\n", "a 1") == "---\nx: 1\n---\n<!-- a 1 -->\nText.\n"
    assert stamp_markdown("# A\n## B\n# C\n", "a 1", titles={"C"}) == "# A\n## B\n# C <!-- a 1 -->\n"
    assert stamp_file("x.py", "import os\n", "a 1") == "import os  # a 1\n"
    assert stamp_file("x.sh", "#!/bin/sh\ncmd \\\n  --flag\n", "a 1") == "#!/bin/sh\ncmd \\\n  --flag\n"
    assert stamp_file("x.json", "{}\n", "a 1") == "{}\n"
    for path, text in [("x.md", "Just text.\n"), ("x.md", "# A\n\nb\n"), ("x.py", "import os\n"),
                       ("x.sh", "#!/bin/sh\n\necho\n")]:
        assert unstamp_file(path, stamp_file(path, text, "a 1"), "a") == text
    assert unstamp_markdown("# A <!-- other 1 -->\n", "a") == "# A <!-- other 1 -->\n"
