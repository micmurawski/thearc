"""Harness-only extension files, and ratings of code files shown as a comment in the ratings view."""

import pytest

from thearcv2.resources import (
    CategoricalScale,
    Extension,
    MetaAgent,
    ReflectionSchema,
    ResourceTarget,
    Skill,
    agent_outline,
    annotated_files,
    detect_changes,
    install,
    read_installation,
    read_workspace,
    write_workspace,
)

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"])
GUARD = "export default function guard(pi) {\n  pi.on('tool_call', () => {});\n}\n"


@pytest.fixture
def agent() -> MetaAgent:
    return MetaAgent(
        name="a",
        extensions=[Extension(harness="pi", path="guard.ts", content=GUARD)],
        skills=[
            Skill(
                name="deploy", instructions="# Deploy\n\nRun it.\n", files={"run.sh": "#!/bin/sh\nset -e\nmake ship\n"}
            )
        ],
    )


def rate(agent: MetaAgent, ref: str, value: str) -> None:
    agent.journal.record(*ReflectionSchema(usefulness).record([{"ref": ref, "rating": value}], agent).entries)


def test_extensions_install_only_into_their_harness(agent, tmp_path):
    pi = install(agent, "pi", tmp_path / "pi")
    assert (tmp_path / "pi" / ".pi" / "extensions" / "guard.ts").read_text() == GUARD
    assert pi.skipped == ()
    claude = install(agent, "claude", tmp_path / "claude")
    assert claude.skipped == ("extension:pi/guard.ts",)
    assert not list((tmp_path / "claude").rglob("guard.ts"))
    assert not (tmp_path / "claude" / ".claude" / "settings.json").exists()


def test_extensions_round_trip_through_installation_and_workspace(agent, tmp_path):
    install(agent, "pi", tmp_path / "project")
    assert read_installation("pi", tmp_path / "project").extensions == agent.extensions
    write_workspace(agent, tmp_path / "ws")
    assert (tmp_path / "ws" / "extensions" / "pi" / "guard.ts").is_file()
    assert read_workspace(tmp_path / "ws") == agent


def test_removed_extension_is_uninstalled(agent, tmp_path):
    install(agent, "pi", tmp_path)
    install(agent.model_copy(update={"extensions": {}}), "pi", tmp_path)
    assert not (tmp_path / ".pi" / "extensions" / "guard.ts").exists()


def test_extensions_are_cited_rated_and_tracked_as_whole_files(agent):
    target = ResourceTarget(kind="extension", name="pi/guard.ts")
    assert agent.files().resolve("extensions/pi/guard.ts#L2") == (target,)
    rate(agent, "extensions/pi/guard.ts#L2", "harmful")
    assert "extension: pi/guard.ts `extensions/pi/guard.ts#L1-L3` (harmful: 1" in agent_outline(agent, usefulness)
    edited = agent.model_copy(deep=True)
    edited.extensions["pi/guard.ts"].content = GUARD.replace("tool_call", "tool_result")
    assert [(c.kind, c.target) for c in detect_changes(agent, edited)] == [("modified", target)]


def test_code_file_rating_is_a_comment_on_its_first_line(agent):
    rate(agent, "extensions/pi/guard.ts#L2", "helpful")
    rate(agent, "skills/deploy/run.sh#L3", "harmful")
    files = annotated_files(agent, usefulness)
    guard = files["extensions/pi/guard.ts"].splitlines()
    assert guard[0] == "export default function guard(pi) {  // rating: (harmful: 0, neutral: 0, helpful: 1)"
    script = files["skills/deploy/run.sh"].splitlines()
    assert script[:2] == ["#!/bin/sh", "set -e  # rating: (harmful: 1, neutral: 0, helpful: 0)"]
    for path, text in files.items():  # Only labelled lines change; references stay valid.
        assert len(text.splitlines()) == len(agent.files().files[path].splitlines())


def test_rating_comment_edge_cases():
    agent = MetaAgent(
        name="a",
        skills=[
            Skill(
                name="s",
                instructions="# S\n",
                files={"blank.py": "\nprint(1)\n", "only.sh": "#!/bin/sh\n", "data.bin.txt": "x\n"},
            )
        ],
    )
    for path in ("skills/s/blank.py#L2", "skills/s/only.sh#L1", "skills/s/data.bin.txt#L1"):
        rate(agent, path, "neutral")
    files = annotated_files(agent, usefulness, ids=True)
    blank_id = ResourceTarget(kind="skill_file", name="s/blank.py").id
    assert files["skills/s/blank.py"].splitlines()[0] == f"# rating: [{blank_id}] (harmful: 0, neutral: 1, helpful: 0)"
    assert files["skills/s/only.sh"] == "#!/bin/sh\n"  # Nothing after the shebang to carry the comment.
    assert files["skills/s/data.bin.txt"] == "x\n"  # No known comment syntax.
