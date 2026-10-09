import pytest

from thearcv2.resources import FileReference, MetaAgent, ResourceTarget, Skill
from thearcv2.resources.markdown import outline, split_frontmatter

SKILL = "skills/retry/SKILL.md"


def section(name: str, kind: str = "skill", resource: str = "retry") -> ResourceTarget:
    return ResourceTarget(kind=kind, name=resource, section=name)


def test_reference_format():
    assert FileReference.parse("a/b.md#L3") == FileReference("a/b.md", 3, 3)
    assert str(FileReference.parse("a/b.md#L3-L9")) == "a/b.md#L3-L9"
    for invalid in ("a.md#0L100", "a.md#L0", "a.md#L5-L2", "a.md", "a.md#3"):
        with pytest.raises(ValueError):
            FileReference.parse(invalid)


def test_layout(agent):
    assert list(agent.files().files) == [
        "context/AGENTS.md",
        "hooks.json",
        "mcp.json",
        "rules/style.md",
        SKILL,
        "skills/retry/notes.md",
        "skills/retry/run.sh",
    ]


def test_skill_markdown_round_trips_body_exactly(agent):
    skill = agent.skills["retry"].model_copy(update={"metadata": {"tags": ["x"]}, "instructions": "\n  body\n\n"})
    frontmatter, body, lines = split_frontmatter(MetaAgent(name="a", skills=[skill]).files().files[SKILL])
    assert frontmatter == {"name": "retry", "description": "Retry things", "tags": ["x"]}
    assert body == skill.instructions
    assert lines == 6


def test_resolution_within_one_section(agent):
    files = agent.files()
    assert files.read(SKILL, 13, 13).endswith("ex1")
    assert files.resolve(f"{SKILL}#L13") == (section("Retry > Examples"),)


def test_resolution_spanning_two_sections_gives_both(agent):
    assert agent.files().resolve(f"{SKILL}#L13-L17") == (section("Retry > Examples"), section("Retry > Limits"))


def test_parent_owns_lines_before_its_first_subsection(agent):
    assert agent.files().resolve(f"{SKILL}#L9-L13") == (section("Retry"), section("Retry > Examples"))


def test_frontmatter_and_preamble_belong_to_the_whole_resource(agent):
    assert agent.files().resolve(f"{SKILL}#L1-L6") == (ResourceTarget(kind="skill", name="retry"),)


def test_structured_entries_resolve_to_their_resource(agent):
    files = agent.files()
    whole = files.resolve(f"hooks.json#L1-L{len(files.files['hooks.json'].splitlines())}")
    assert whole == (ResourceTarget(kind="hook", name="guard"), ResourceTarget(kind="hook", name="lint"))
    with pytest.raises(ValueError, match="does not cover"):
        files.resolve("hooks.json#L1")


def test_unresolvable_references(agent):
    files = agent.files()
    with pytest.raises(ValueError, match="unknown file"):
        files.resolve("skills/nope/SKILL.md#L1")
    with pytest.raises(ValueError, match="has 17 lines"):
        files.resolve(f"{SKILL}#L18")


def test_repeated_heading_paths_are_owned_by_the_parent():
    agent = MetaAgent(name="a", skills=[Skill(name="s", instructions="# A\n\n## X\n\none\n\n## X\n\ntwo\n")])
    files = agent.files()
    assert section("A", resource="s") in files.targets
    assert all(t.section != "A > X" for t in files.targets)
    assert files.resolve("skills/s/SKILL.md#L10") == (section("A", resource="s"),)


def test_same_title_under_different_parents_is_addressable():
    headings = outline("# Usage\n## Examples\n# Setup\n## Examples\n")
    assert [h.address for h in headings] == ["Usage", "Usage > Examples", "Setup", "Setup > Examples"]


def test_headings_in_code_fences_are_ignored():
    assert [h.address for h in outline("# A\n```\n# not a heading\n```\n")] == ["A"]


def test_revision_is_version_at_content_hash(agent, human):
    content = agent.files().revision
    assert agent.revision == f"1@{content}" and content.startswith("rev_")
    assert agent.model_copy(update={"name": "other"}).revision == agent.revision  # Name and journal don't count.
    assert agent.model_copy(update={"version": "2"}).revision == f"2@{content}"
    assert agent.model_copy(update={"version": None}).revision == content
    edited = agent.model_copy(deep=True)
    edited.context["AGENTS.md"].content += "More.\n"
    assert edited.revision != agent.revision


def test_diff(agent):
    edited = agent.model_copy(deep=True)
    edited.context["AGENTS.md"].content = "# Rules\n\nBe precise.\n"
    diff = agent.diff(edited)
    assert "-Be brief." in diff and "+Be precise." in diff
    assert agent.diff(agent) == ""
