from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from thearc.models.ranks import Ranks, split_ranked_title


class MarkdownSection(BaseModel):
    """
    Represents a section in a Markdown document bounded by a header (e.g. #, ##, ###).
    """
    title: str
    ranks: Ranks | None = None
    level: int = 1
    content: str = ""
    subsections: list[MarkdownSection] = Field(default_factory=list)

    @property
    def raw_header(self) -> str:
        return f"{'#' * self.level} {self.title}"

    def get_header(self, title: str, exact: bool = False) -> MarkdownSection | None:
        """
        Search recursively within this section and its subsections for a matching header title.
        """
        search_term = title.strip().lower()
        target_title = self.title.strip().lower()

        if exact:
            if target_title == search_term:
                return self
        else:
            if search_term in target_title or self.slug == search_term:
                return self

        for sub in self.subsections:
            found = sub.get_header(title, exact=exact)
            if found:
                return found

        return None

    @property
    def slug(self) -> str:
        """Generate URL/anchor slug for title."""
        s = self.title.lower().strip()
        s = re.sub(r'[^\w\s-]', '', s)
        return re.sub(r'[-\s]+', '-', s)

    def full_content(self, include_ranks: bool = True) -> str:
        """Return content of this section including all rendered subsections."""
        parts = [self.to_markdown(include_ranks=include_ranks)]
        return "\n\n".join(parts)

    def to_markdown(self, include_ranks: bool = True) -> str:
        """Render section and child subsections back to markdown format."""
        header_line = f"{'#' * self.level} {self.title}"
        if include_ranks and self.ranks is not None:
            header_line += self.ranks.suffix()
        lines = [header_line]
        if self.content:
            lines.append(self.content)
        
        rendered = "\n\n".join(lines)
        if self.subsections:
            sub_rendered = "\n\n".join([s.to_markdown(include_ranks=include_ranks) for s in self.subsections])
            rendered = f"{rendered}\n\n{sub_rendered}"

        return rendered


class MarkdownDocument(BaseModel):
    """
    Object representation of a Markdown document (e.g., AGENTS.md, CLAUDE.md, GEMINI.md).
    Supports hierarchy parsing (# -> ## -> ###), frontmatter metadata, header querying, and serialization.
    """
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    root_content: str = ""
    sections: list[MarkdownSection] = Field(default_factory=list)

    @classmethod
    def read(cls, path: str | Path) -> MarkdownDocument:
        """Read and parse a markdown file from disk."""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Markdown file not found: {path}")
        text = p.read_text(encoding="utf-8")
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> MarkdownDocument:
        """Parse raw markdown string into a MarkdownDocument object representation."""
        frontmatter = {}
        content_text = text

        # Parse YAML Frontmatter if present
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                    content_text = parts[2]
                except yaml.YAMLError:
                    content_text = text

        lines = content_text.splitlines()
        header_pattern = re.compile(r'^(#{1,6})\s+(.+)$')

        # Find first header index
        first_header_idx = None
        for idx, line in cls._heading_lines(lines):
            if header_pattern.match(line):
                first_header_idx = idx
                break

        if first_header_idx is None:
            # No headers present in doc
            return cls(frontmatter=frontmatter, root_content=content_text.strip())

        root_content = "\n".join(lines[:first_header_idx]).strip()

        # Parse sections hierarchically
        section_lines = lines[first_header_idx:]
        sections = cls._parse_section_blocks(section_lines)

        return cls(frontmatter=frontmatter, root_content=root_content, sections=sections)

    @staticmethod
    def _heading_lines(lines: list[str]):
        """Yield candidate headings outside fenced and indented code blocks."""
        fence = None
        for idx, line in enumerate(lines):
            if fence is not None:
                if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                    fence = None
                continue
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening:
                fence = opening[1]
                continue
            if not line.startswith(("    ", "\t")):
                yield idx, line.lstrip(" ")

    @classmethod
    def _parse_section_blocks(cls, lines: list[str]) -> list[MarkdownSection]:
        header_pattern = re.compile(r'^(#{1,6})\s+(.+)$')
        blocks: list[dict[str, Any]] = []

        current_level = None
        current_title = None
        current_lines: list[str] = []

        heading_lines = dict(cls._heading_lines(lines))
        for idx, line in enumerate(lines):
            match = header_pattern.match(heading_lines.get(idx, ""))
            if match:
                if current_title is not None:
                    blocks.append({
                        "level": current_level,
                        "title": current_title,
                        "content": "\n".join(current_lines).strip()
                    })
                current_level = len(match.group(1))
                current_title = match.group(2).strip()
                current_lines = []
            else:
                if current_title is not None:
                    current_lines.append(line)

        if current_title is not None:
            blocks.append({
                "level": current_level,
                "title": current_title,
                "content": "\n".join(current_lines).strip()
            })

        return cls._build_hierarchy(blocks)

    @classmethod
    def _build_hierarchy(cls, raw_blocks: list[dict[str, Any]]) -> list[MarkdownSection]:
        if not raw_blocks:
            return []

        # Find top level in these blocks
        min_level = min(b["level"] for b in raw_blocks)
        top_sections: list[MarkdownSection] = []

        i = 0
        while i < len(raw_blocks):
            block = raw_blocks[i]
            if block["level"] == min_level:
                # Find all subsequent blocks that belong to this subsection
                j = i + 1
                child_blocks = []
                while j < len(raw_blocks) and raw_blocks[j]["level"] > min_level:
                    child_blocks.append(raw_blocks[j])
                    j += 1
                
                subsections = cls._build_hierarchy(child_blocks)
                title, ranks = split_ranked_title(block["title"])
                sec = MarkdownSection(
                    title=title,
                    ranks=ranks,
                    level=block["level"],
                    content=block["content"],
                    subsections=subsections
                )
                top_sections.append(sec)
                i = j
            else:
                i += 1

        return top_sections

    def get_header(self, title: str, exact: bool = False) -> MarkdownSection | None:
        """
        Find a section header by title or slug anywhere in the document.
        Usage: doc.get_header("Coding Standards")
        """
        for sec in self.sections:
            found = sec.get_header(title, exact=exact)
            if found:
                return found
        return None

    def get_section(self, title: str, exact: bool = False) -> MarkdownSection | None:
        """Alias for get_header."""
        return self.get_header(title, exact=exact)

    def add_section(
        self,
        title: str,
        content: str = "",
        level: int = 1,
        parent_title: str | None = None,
        ranks: Ranks | None = None,
    ) -> MarkdownSection:
        """Add a new section to the document or under a specified parent section."""
        new_sec = MarkdownSection(title=title, level=level, content=content, ranks=ranks)
        if parent_title:
            parent = self.get_header(parent_title)
            if parent:
                new_sec.level = parent.level + 1
                parent.subsections.append(new_sec)
                return new_sec
        self.sections.append(new_sec)
        return new_sec

    def remove_section(self, title: str) -> bool:
        """Remove a section by title from the document."""
        search_term = title.strip().lower()

        # Check top level
        for idx, sec in enumerate(self.sections):
            if sec.title.strip().lower() == search_term or sec.slug == search_term:
                self.sections.pop(idx)
                return True

        # Check recursively in subsections
        def _remove_from_subsections(parent_sec: MarkdownSection) -> bool:
            for idx, sub in enumerate(parent_sec.subsections):
                if sub.title.strip().lower() == search_term or sub.slug == search_term:
                    parent_sec.subsections.pop(idx)
                    return True
                if _remove_from_subsections(sub):
                    return True
            return False

        for sec in self.sections:
            if _remove_from_subsections(sec):
                return True

        return False

    def to_markdown(self, include_ranks: bool = True) -> str:
        """Serialize the entire Markdown document back to string format."""
        parts: list[str] = []

        frontmatter = dict(self.frontmatter)
        if not include_ranks:
            frontmatter.pop("ranks", None)
        if frontmatter:
            fm_str = yaml.dump(frontmatter, sort_keys=False).strip()
            parts.append(f"---\n{fm_str}\n---")

        if self.root_content:
            parts.append(self.root_content)

        if self.sections:
            parts.append("\n\n".join([sec.to_markdown(include_ranks=include_ranks) for sec in self.sections]))

        return "\n\n".join(parts) + "\n"

    def write(self, path: str | Path, include_ranks: bool = True) -> None:
        """Write serialized markdown document to disk."""
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_markdown(include_ranks=include_ranks), encoding="utf-8")
