import json

import pytest

from thearc import MCP, Hook, MetaAgent, Skill
from thearc.models import Ranks, ResourceTarget

TEXT = """# Rules (harmful: 1, neutral: 0, helpful: 3)
Be brief
## Style (harmful: 0, neutral: 2, helpful: 5)
Short
# Notes
One
# Notes
Two
"""


def agent():
    return MetaAgent(
        name="a",
        context={"AGENTS.md": {"content": TEXT}},
        skills=[
            Skill(
                name="debug",
                instructions="# Retry\nInspect errors\n# Verify\nRun tests",
                files={"run.sh": "echo", "notes/tips.md": "# Tip\nRead logs"},
            )
        ],
        hooks=[Hook(name="lint", command="ruff"), Hook(name="fmt", command="black")],
        mcps=[MCP(name="docs", command="docs-server")],
    )


def target(section=None, kind="context", name="AGENTS.md"):
    return ResourceTarget(kind=kind, name=name, section=section)


def test_ids_are_stable_addresses_independent_of_ranks():
    assert target("Rules").id == "res_90c712f1620444013b827105"
    assert {t.id for t, _ in agent().iter_targets()} == {t.id for t, _ in agent().without_ranks().iter_targets()}


def test_iter_targets_nests_sections_and_skips_ambiguous_headings():
    listed = [(t.kind, t.name, t.section, depth) for t, depth in agent().iter_targets()]
    assert ("context", "AGENTS.md", None, 0) in listed
    assert listed.index(("context", "AGENTS.md", "Rules", 1)) < listed.index(("context", "AGENTS.md", "Style", 2))
    assert not any(section == "Notes" for _, _, section, _ in listed)
    assert ("skill_file", "debug/run.sh", None, 0) in listed


def test_files_are_rank_free_and_every_target_has_lines():
    files = agent().files()
    assert set(files.files) == {
        "skills/debug/SKILL.md",
        "skills/debug/run.sh",
        "skills/debug/notes/tips.md",
        "context/AGENTS.md",
        "hooks.json",
        "mcp.json",
    }
    assert "harmful" not in "".join(files.files.values())
    assert {span.target for span in files.spans} == {t for t, _ in agent().iter_targets()}
    hooks = json.loads(files.files["hooks.json"])
    assert hooks["lint"]["command"] == "ruff" and "ranks" not in hooks["lint"]
    assert files.read("context/AGENTS.md", 1, 2) == "1 | # Rules\n2 | "


def test_references_resolve_to_innermost_section():
    files = agent().files()
    rules, style = files.reference_for(target("Rules")), files.reference_for(target("Style"))
    style_start = int(style.split("#L")[1].split("-")[0])
    assert files.resolve(f"context/AGENTS.md#L{style_start}") == target("Style")
    assert files.resolve(rules) == target("Rules")
    assert files.resolve("context/AGENTS.md#L2") == target("Rules")
    notes = files.files["context/AGENTS.md"].splitlines().index("# Notes") + 1
    assert files.resolve(f"context/AGENTS.md#L{notes}") == target()  # ambiguous heading → whole file
    assert files.resolve("skills/debug/run.sh#L1") == target(kind="skill_file", name="debug/run.sh")
    lint = files.reference_for(target(kind="hook", name="lint"))
    assert files.resolve(lint) == target(kind="hook", name="lint")


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("context/AGENTS.md#L2-L{style}", "crosses the boundary"),
        ("context/AGENTS.md#L99", "lines"),
        ("missing.md#L1", "Unknown file"),
        ("context/AGENTS.md", "Invalid reference"),
        ("hooks.json#L1", "not inside one resource"),
    ],
)
def test_ambiguous_or_invalid_references_are_rejected(reference, message):
    files = agent().files()
    style = int(files.reference_for(target("Style")).split("#L")[1].split("-")[0])
    with pytest.raises(ValueError, match=message):
        files.resolve(reference.format(style=style))


def test_sections_show_ids_and_ranks_resources_show_files():
    text = agent().to_labeled_markdown(include_ranks=True)
    rules, style = target("Rules").id, target("Style").id
    assert f"# Rules [SectionId: {rules}, ranks: (harmful: 1, neutral: 0, helpful: 3)]\n\nBe brief" in text
    assert f"## Style [SectionId: {style}, ranks: (harmful: 0, neutral: 2, helpful: 5)]\n\nShort" in text
    assert "# context: AGENTS.md [context/AGENTS.md, ranks: (" in text and "ResourceId" not in text
    assert "# Notes\n\nOne" in text  # duplicate headings have no ID of their own
    assert "# mcp: docs [mcp.json#L" in text and "```\necho\n```" in text
    plain = agent().to_labeled_markdown()
    assert "ranks" not in plain and f"# Rules [SectionId: {rules}]" in plain


def test_outline_lists_section_ids_with_depth_and_custom_ranks():
    outline = agent().to_labeled_markdown(outline=True, include_ranks=True, ranks=lambda t: Ranks(helpful=7))
    assert f"  - Style [SectionId: {target('Style').id}, ranks: (harmful: 0, neutral: 0, helpful: 7)]" in outline
    assert "Be brief" not in outline


def test_reference_lands_in_section_whose_rank_is_counted():
    files = agent().files()
    style = files.reference_for(target("Style"))
    assert files.resolve(style).id == target("Style").id
