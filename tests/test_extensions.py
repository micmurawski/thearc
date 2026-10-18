"""Harness-only extension files, and ratings of code files shown as a comment in the ratings view."""

import pytest

from thearc.resources import (
    CategoricalScale,
    Extension,
    MetaAgent,
    ReflectionSchema,
    ResourceTarget,
    Skill,
    agent_outline,
    detect_changes,
    install,
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
    assert MetaAgent.from_installation("pi", tmp_path / "project").extensions == agent.extensions
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
