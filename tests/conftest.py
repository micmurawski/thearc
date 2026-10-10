import pytest

from thearc.resources import MCP, AgentResource, ContextDocument, Hook, MetaAgent, Skill

RETRY = """Intro line

# Retry

Use retries.

## Examples

ex1

## Limits

max 3
"""


@pytest.fixture
def agent() -> MetaAgent:
    return MetaAgent(
        name="demo",
        version="1",
        skills=[
            Skill(
                name="retry",
                description="Retry things",
                instructions=RETRY,
                files={"notes.md": "# Notes\n\nkeep\n", "run.sh": "echo hi\n"},
            )
        ],
        hooks=[Hook(name="guard", command="./guard.sh"), Hook(name="lint", event="post_tool_use", command="ruff")],
        mcps=[MCP(name="docs", url="https://example.test/mcp", transport="http")],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nBe brief.\n")],
        resources=[AgentResource(location="rules", path="style.md", content="# Style\n\nNo emoji.\n")],
    )


@pytest.fixture
def human() -> dict:
    """Keyword arguments stored on journal entries as ``extra``."""
    return {"by": "tester"}


@pytest.fixture
def reviewed(agent, tmp_path):
    """A workspace whose journal holds one reflection rating ``Retry > Limits`` harmful."""
    from thearc.reflection import Reflector

    from .test_reflection import GOOD, SCHEMA, Scripted, answer

    workspace = agent.init_workspace(tmp_path / "ws")
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", workspace, SCHEMA)
    workspace.add_reflection(reflection)
    return workspace, reflection
