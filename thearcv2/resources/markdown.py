"""The Markdown structure needed to address sections: frontmatter and the heading outline.

Text is never re-rendered from a parsed tree, so the bytes of a document survive a
round trip exactly and line numbers stay stable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import yaml

SECTION_SEPARATOR = " > "
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, int]:
    """Return ``(frontmatter, body, frontmatter_line_count)``; ``None`` when there is no valid frontmatter."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text, 0
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            try:
                data = yaml.safe_load("".join(lines[1:index]))
            except yaml.YAMLError:
                return None, text, 0
            if data is None:
                data = {}
            if not isinstance(data, dict):
                return None, text, 0
            return data, "".join(lines[index + 1:]), index + 1
    return None, text, 0


def render_frontmatter(data: dict[str, Any], body: str) -> str:
    """Prepend YAML frontmatter to ``body`` so that ``split_frontmatter`` returns both unchanged."""
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=float("inf")) + "---\n" + body


@dataclass(frozen=True)
class Heading:
    """An ATX heading and the lines of its section, 1-based and inclusive.

    ``end`` includes subsections. ``path`` holds the titles of the heading and its
    ancestors, which is what makes ``Usage > Examples`` and ``Setup > Examples``
    two different addresses.
    """

    path: tuple[str, ...]
    level: int
    start: int
    end: int

    @property
    def address(self) -> str:
        return SECTION_SEPARATOR.join(self.path)


def _heading_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """Yield candidate headings outside fenced and indented code blocks."""
    fence = None
    for index, line in enumerate(lines):
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening:
            fence = opening[1]
            continue
        if not line.startswith(("    ", "\t")):
            yield index, line.lstrip(" ")


def top_sections(text: str) -> list[tuple[str, str]]:
    """``(title, text)`` of each section at the document's highest heading level, in order.

    Each section's text ends with exactly one newline. Text before the first
    heading is not part of any section.
    """
    headings = outline(text)
    if not headings:
        return []
    level = min(heading.level for heading in headings)
    lines = text.splitlines(keepends=True)
    return [(heading.path[-1], "".join(lines[heading.start - 1:heading.end]).rstrip() + "\n")
            for heading in headings if heading.level == level]


def _find(text: str, title: str) -> Heading | None:
    """The first, highest-level heading titled ``title`` (case-insensitive)."""
    matches = [h for h in outline(text) if h.path[-1].strip().casefold() == title.strip().casefold()]
    return min(matches, key=lambda h: (h.level, h.start)) if matches else None


def section_text(text: str, title: str) -> str | None:
    """The text of the section ``replace_sections`` would match for ``title``, ending with one newline."""
    heading = _find(text, title)
    if heading is None:
        return None
    return "".join(text.splitlines(keepends=True)[heading.start - 1:heading.end]).rstrip() + "\n"


def replace_sections(text: str, sections: list[tuple[str, str]], *, remove: tuple[str, ...] = ()) -> str:
    """Upsert ``sections`` into ``text`` by heading title and delete the sections titled in ``remove``.

    A section whose title already exists replaces it in place; a new one is
    appended. Everything else in ``text`` is kept as it is.
    """
    for title in remove:
        heading = _find(text, title)
        if heading is not None:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:heading.start - 1] + lines[heading.end:])
    for title, section in sections:
        heading = _find(text, title)
        if heading is None:
            text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + section
            continue
        lines = text.splitlines(keepends=True)
        after = lines[heading.end:]
        text = "".join(lines[:heading.start - 1]) + section + ("\n" if after else "") + "".join(after)
    return text


def outline(text: str, *, first_line: int = 1) -> list[Heading]:
    """Headings of ``text`` in document order; ``first_line`` is the file line ``text`` starts on."""
    lines = text.splitlines()
    found: list[tuple[int, int, str]] = []
    for index, line in _heading_lines(lines):
        match = _HEADING.match(line)
        if match:
            found.append((index + first_line, len(match[1]), match[2].strip()))
    last_line = first_line + len(lines) - 1
    headings: list[Heading] = []
    stack: list[Heading] = []
    for position, (start, level, title) in enumerate(found):
        end = next((line - 1 for line, other, _ in found[position + 1:] if other <= level), last_line)
        while stack and stack[-1].level >= level:
            stack.pop()
        heading = Heading(path=(*(stack[-1].path if stack else ()), title), level=level, start=start, end=end)
        headings.append(heading)
        stack.append(heading)
    return headings
