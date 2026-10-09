"""Reflection output as Markdown: objects as headings, flat uniform lists as tables, the rest as lists."""

from thearc.reflection import Reflector
from thearc.reflection.markdown import title, to_markdown

from .test_reflection import Scripted
from .test_reflection_shapes import ANSWER, LESSON


def test_a_lesson_reads_as_sections_with_a_ratings_table(agent):
    reflection = Reflector(runtime=Scripted([ANSWER])).reflect("Review.", agent, LESSON)
    assert reflection.to_markdown() == (
        "## Reasoning\n\nThe agent reran a SyntaxError ten times.\n\n"
        "## Error Identification\n\nIt treated a permanent error as transient.\n\n"
        "## Key Insight\n\nFix permanent errors; retry only transient ones.\n\n"
        "## Ratings\n\n"
        "| Ref | Usefulness |\n| --- | --- |\n"
        "| `skills/retry/SKILL.md#L17` | harmful |\n| `context/AGENTS.md#L3` | helpful |\n\n"
        "## See Also\n\n- `skills/retry/SKILL.md#L9`\n"
    )


def test_primitives_lists_and_empty_values():
    assert to_markdown({"done": True, "skipped": False, "score": 0.5, "owner": None, "tags": [], "extra": {}}) == (
        "## Done\n\nyes\n\n## Skipped\n\nno\n\n## Score\n\n0.5\n\n## Owner\n\n—\n\n"
        "## Tags\n\n*none*\n\n## Extra\n\n*none*\n"
    )
    assert to_markdown(["a", "line one\nline two"]) == "- a\n- line one\n  line two\n"


def test_lists_that_do_not_fit_a_table_are_numbered():
    uneven = [{"ref": "a.md#L1", "tag": "helpful"}, {"ref": "a.md#L2", "why": "x"}]
    assert to_markdown({"ratings": uneven}) == (
        "## Ratings\n\n### 1.\n\n#### Ref\n\n`a.md#L1`\n\n#### Tag\n\nhelpful\n\n"
        "### 2.\n\n#### Ref\n\n`a.md#L2`\n\n#### Why\n\nx\n"
    )
    long_cells = [{"note": "first\nsecond"}]
    assert "| Note |" not in to_markdown({"notes": long_cells})


def test_deep_structure_becomes_bullets_and_cells_are_escaped():
    deep = {"a": {"b": {"c": {"d": "value", "e": [1, {"f": None}]}}}}
    assert to_markdown(deep) == (
        "## A\n\n### B\n\n#### C\n\n- **D:** value\n- **E:**\n  - 1\n  -\n    - **F:** —\n"
    )
    assert "a \\| b" in to_markdown({"rows": [{"x": "a | b"}]})
    assert title("keyInsight") == "Key Insight" and title("root_cause_analysis") == "Root Cause Analysis"


def test_tables_work_below_the_deepest_heading():
    assert to_markdown({"ratings": [{"ref": "a.md#L1", "tag": "helpful"}]}, level=4) == (
        "#### Ratings\n\n| Ref | Tag |\n| --- | --- |\n| `a.md#L1` | helpful |\n"
    )
