from thearc.models import (
    MarkdownDocument,
    SkillDefinition,
    SkillMetadata,
    get_strategy,
)

SAMPLE_MARKDOWN = """---
name: sample_doc
version: 1.0.0
---

Introduction text before any headers.

# Coding Guidelines

Follow PEP 8 rules for python code.

## Header Bleach

This is the content of header bleach.

### Deep Subtitle

Nested content inside deep subtitle.

## Unit Testing

Always write unit tests for new features.

# Deployment

Instructions for deployment to staging and production.
"""

def test_markdown_document_parsing():
    doc = MarkdownDocument.parse(SAMPLE_MARKDOWN)
    assert doc.frontmatter["name"] == "sample_doc"
    assert "Introduction text" in doc.root_content
    assert len(doc.sections) == 2  # Coding Guidelines & Deployment

    # Retrieve Level 1 Header
    coding_sec = doc.get_header("Coding Guidelines")
    assert coding_sec is not None
    assert coding_sec.level == 1
    assert "Follow PEP 8 rules" in coding_sec.content

    # Retrieve Level 2 Header
    bleach_sec = doc.get_header("Header Bleach")
    assert bleach_sec is not None
    assert bleach_sec.level == 2
    assert bleach_sec.content == "This is the content of header bleach."

    # Retrieve Level 3 Nested Header
    deep_sec = doc.get_header("Deep Subtitle")
    assert deep_sec is not None
    assert deep_sec.level == 3
    assert deep_sec.content == "Nested content inside deep subtitle."


def test_markdown_document_add_and_remove(tmp_path):
    doc = MarkdownDocument.parse(SAMPLE_MARKDOWN)
    
    # Add new section
    doc.add_section("New Custom Section", content="Custom content text.", level=1)
    new_sec = doc.get_header("New Custom Section")
    assert new_sec is not None
    assert new_sec.content == "Custom content text."

    # Write to disk & re-read
    file_path = tmp_path / "AGENTS.md"
    doc.write(file_path)
    
    reloaded_doc = MarkdownDocument.read(file_path)
    assert reloaded_doc.get_header("New Custom Section") is not None

    # Remove section
    removed = reloaded_doc.remove_section("Header Bleach")
    assert removed is True
    assert reloaded_doc.get_header("Header Bleach") is None


def test_pydantic_skill_models():
    meta = SkillMetadata(name="test_skill", description="Testing skill model", version="2.0.0")
    yaml_out = meta.to_yaml()
    assert "name: test_skill" in yaml_out
    assert "description: Testing skill model" in yaml_out

    skill = SkillDefinition(metadata=meta, instructions="Execute test script.")
    
    # Test Antigravity Strategy
    ag_strat = get_strategy("antigravity")
    ag_md = ag_strat.skill_to_markdown(skill)
    assert "name: test_skill" in ag_md
    assert "Execute test script." in ag_md

    # Test Claude Strategy
    cl_strat = get_strategy("claude")
    cl_md = cl_strat.skill_to_markdown(skill)
    assert "# Skill: test_skill" in cl_md

    # Test Codex Strategy
    cx_strat = get_strategy("codex")
    cx_md = cx_strat.skill_to_markdown(skill)
    assert "<!-- Skill: test_skill" in cx_md
