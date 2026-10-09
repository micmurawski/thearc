"""Markdown for JSON values: how a reflection's output (any schema) reads as a document.

| JSON | Markdown |
| --- | --- |
| object | each key a heading one level deeper (``error_identification`` → ``## Error Identification``); past
  level 4, ``- **Key:** value`` bullets |
| string | as written (it is prose and may hold Markdown); a whole-string file reference as code |
| number / true, false / null | as written / yes, no / — |
| list of primitives | a bullet list |
| list of objects with the same short, flat fields | a table, one row per object |
| other list | numbered entries |
| empty list or object | *none* |
"""

from __future__ import annotations

import re
from typing import Any

from thearc.resources.reflection import REFERENCE_PATTERN

_REF = re.compile(REFERENCE_PATTERN)
_DEEPEST_HEADING = 4
_CELL = 100  # Longer strings, or ones with line breaks, make a list too wide for a table.


def to_markdown(value: Any, *, level: int = 2) -> str:
    """``value`` as Markdown; objects start with headings of ``level``."""
    return "\n".join(_block(value, level)).strip() + "\n"


def title(key: str) -> str:
    """``error_identification`` → ``Error Identification``; ``keyInsight`` → ``Key Insight``."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ").replace("-", " ").split()
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def _primitive(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _inline(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and _REF.match(value):
        return f"`{value}`"
    return str(value)


def _short(value: Any) -> bool:
    return _primitive(value) and "\n" not in str(value) and len(str(value)) <= _CELL


def _table(rows: list[Any]) -> list[str] | None:
    """A table when every row is an object with the same short, flat fields."""
    if not rows or not all(isinstance(row, dict) and row for row in rows):
        return None
    keys = list(rows[0])
    if any(list(row) != keys or not all(_short(cell) for cell in row.values()) for row in rows):
        return None

    def cell(value: Any) -> str:
        return _inline(value).replace("|", "\\|")

    return [
        "| " + " | ".join(title(key) for key in keys) + " |",
        "| " + " | ".join("---" for _ in keys) + " |",
        *("| " + " | ".join(cell(row[key]) for key in keys) + " |" for row in rows),
    ]


def _block(value: Any, level: int) -> list[str]:
    if isinstance(value, list) and value and (table := _table(value)) is not None:
        return [*table, ""]  # A table needs no heading, so it works at any depth.
    if level > _DEEPEST_HEADING:
        return [*_bullets(value, 0), ""]
    if isinstance(value, dict):
        if not value:
            return ["*none*", ""]
        lines: list[str] = []
        for key, item in value.items():
            lines += [f"{'#' * level} {title(key)}", "", *_block(item, level + 1)]
        return lines
    if isinstance(value, list):
        if not value:
            return ["*none*", ""]
        if all(_primitive(item) for item in value):
            return [*(f"- {_indented(_inline(item), 2)}" for item in value), ""]
        lines = []
        for number, item in enumerate(value, start=1):
            if isinstance(item, dict | list) and level < _DEEPEST_HEADING:
                lines += [f"{'#' * level} {number}.", "", *_block(item, level + 1)]
            else:
                lines += [f"{number}. {_indented(_inline(item), 3)}" if _primitive(item) else f"{number}.",
                          *([] if _primitive(item) else _bullets(item, 3)), ""]
        return lines
    return [_inline(value), ""]


def _bullets(value: Any, indent: int) -> list[str]:
    """Nested bullets, for structure too deep for headings."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{pad}- *none*"]
        lines = []
        for key, item in value.items():
            if _primitive(item):
                lines.append(f"{pad}- **{title(key)}:** {_indented(_inline(item), indent + 2)}")
            else:
                lines += [f"{pad}- **{title(key)}:**", *_bullets(item, indent + 2)]
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{pad}- *none*"]
        lines = []
        for item in value:
            if _primitive(item):
                lines.append(f"{pad}- {_indented(_inline(item), indent + 2)}")
            else:
                lines += [f"{pad}-", *_bullets(item, indent + 2)]
        return lines
    return [f"{pad}{_indented(_inline(value), indent)}"]


def _indented(text: str, indent: int) -> str:
    """Continuation lines of a multi-line string, indented to stay inside their list item."""
    return text.replace("\n", "\n" + " " * indent)
