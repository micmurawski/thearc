from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel, Field

from thearc.models.markdown import MarkdownDocument, MarkdownSection
from thearc.models.ranks import Ranks


class SectionChange(BaseModel):
    """Represents a detected modification between two versions of a section."""
    section_id: str
    title: str
    old_content: str
    new_content: str
    old_ranks: Ranks | None = None
    new_ranks: Ranks | None = None
    old_level: int
    new_level: int

    @property
    def is_ranks_changed(self) -> bool:
        return self.old_ranks != self.new_ranks

    @property
    def is_content_changed(self) -> bool:
        return self.old_content.strip() != self.new_content.strip()

    @property
    def is_level_changed(self) -> bool:
        return self.old_level != self.new_level

    @property
    def diff_text(self) -> str:
        """Unified diff string of content changes."""
        old_lines = self.old_content.splitlines(keepends=True)
        new_lines = self.new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{self.section_id}",
            tofile=f"b/{self.section_id}"
        )
        return "".join(diff)


class SectionDiffResult(BaseModel):
    """Container for comparison results between two MDFile versions."""
    added_sections: list[IndexedSection] = Field(default_factory=list)
    removed_sections: list[IndexedSection] = Field(default_factory=list)
    modified_sections: list[SectionChange] = Field(default_factory=list)
    unchanged_sections: list[IndexedSection] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added_sections or self.removed_sections or self.modified_sections)

    def summary(self) -> str:
        return (
            f"Diff Summary: {len(self.added_sections)} added, "
            f"{len(self.removed_sections)} removed, "
            f"{len(self.modified_sections)} modified, "
            f"{len(self.unchanged_sections)} unchanged."
        )


class IndexedSection(BaseModel):
    """
    Representation of an indexed section with a unique ID and hierarchy tracking.
    """
    id: str
    title: str
    level: int = 1
    content: str = ""
    ranks: Ranks | None = None
    parent_id: str | None = None
    subsections: list[IndexedSection] = Field(default_factory=list)

    @property
    def raw_header(self) -> str:
        return f"{'#' * self.level} {self.title}"

    def to_markdown(self, include_ranks: bool = True) -> str:
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


class MDFile(BaseModel):
    """
    Class to track, modify, compare, and index Markdown files.
    """
    file_path: Path | None = None
    document: MarkdownDocument = Field(default_factory=MarkdownDocument)
    indexed_sections: dict[str, IndexedSection] = Field(default_factory=dict)

    def __init__(self, path: str | Path | None = None, text: str | None = None, **data):
        if path is not None and text is None:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                raise FileNotFoundError(f"File not found: {path}")
            doc = MarkdownDocument.read(p)
            data["file_path"] = p
            data["document"] = doc
        elif text is not None:
            doc = MarkdownDocument.parse(text)
            data["file_path"] = Path(path).expanduser().resolve() if path else None
            data["document"] = doc

        super().__init__(**data)
        self._index_all_sections()

    def _index_all_sections(self):
        """Indexes all sections in document and assigns unique hierarchical section IDs."""
        self.indexed_sections.clear()

        def _index_node(section: MarkdownSection, parent_path: str = ""):
            slug = section.slug
            sec_id = f"{parent_path}/{slug}" if parent_path else slug

            # Handle duplicate section IDs by appending numeric index
            base_id = sec_id
            counter = 1
            while sec_id in self.indexed_sections:
                counter += 1
                sec_id = f"{base_id}-{counter}"

            indexed = IndexedSection(
                id=sec_id,
                title=section.title,
                ranks=section.ranks,
                level=section.level,
                content=section.content,
                parent_id=parent_path or None
            )

            for sub in section.subsections:
                child_indexed = _index_node(sub, parent_path=sec_id)
                indexed.subsections.append(child_indexed)

            self.indexed_sections[sec_id] = indexed
            return indexed

        for sec in self.document.sections:
            _index_node(sec)

    def get_section(self, id_or_title: str) -> IndexedSection | None:
        """Find an indexed section by its ID or title (case-insensitive)."""
        if id_or_title in self.indexed_sections:
            return self.indexed_sections[id_or_title]

        search_str = id_or_title.strip().lower()
        for sec in self.indexed_sections.values():
            if sec.title.lower() == search_str or sec.id.lower() == search_str:
                return sec
        return None

    def update_section(
        self,
        id_or_title: str,
        content: str | None = None,
        title: str | None = None,
        level: int | None = None
    ) -> bool:
        """Modify an existing section's content, title, or level."""
        target = self.get_section(id_or_title)
        if not target:
            return False

        doc_sec = self.document.get_header(target.title, exact=True)
        if doc_sec:
            if content is not None:
                doc_sec.content = content.strip()
                target.content = content.strip()
            if title is not None:
                doc_sec.title = title.strip()
                target.title = title.strip()
            if level is not None:
                doc_sec.level = level
                target.level = level

            self._index_all_sections()
            return True
        return False

    def add_section(
        self,
        title: str,
        content: str = "",
        level: int = 1,
        parent_id_or_title: str | None = None
    ) -> IndexedSection:
        """Add a new section to the document and index it."""
        parent_title = None
        if parent_id_or_title:
            parent = self.get_section(parent_id_or_title)
            if parent:
                parent_title = parent.title

        self.document.add_section(title=title, content=content, level=level, parent_title=parent_title)
        self._index_all_sections()

        new_sec = self.get_section(title)
        if not new_sec:
            sec_id = list(self.indexed_sections.keys())[-1]
            new_sec = self.indexed_sections[sec_id]
        return new_sec

    def remove_section(self, id_or_title: str) -> bool:
        """Remove a section by ID or title."""
        target = self.get_section(id_or_title)
        if not target:
            return False

        removed = self.document.remove_section(target.title)
        if removed:
            self._index_all_sections()
            return True
        return False

    def compare(self, new_version: MDFile | Path | str) -> SectionDiffResult:
        """
        Compare current MDFile instance against another MDFile instance, file path, or raw markdown string.
        Returns a SectionDiffResult containing added, removed, modified, and unchanged sections.
        """
        if isinstance(new_version, MDFile):
            other_md = new_version
        elif isinstance(new_version, (str, Path)):
            p = Path(new_version)
            if p.exists() and p.is_file():
                other_md = MDFile(path=p)
            else:
                other_md = MDFile(text=str(new_version))
        else:
            raise TypeError(f"Invalid type for comparison target: {type(new_version)}")

        self_ids = set(self.indexed_sections.keys())
        other_ids = set(other_md.indexed_sections.keys())

        added_ids = other_ids - self_ids
        removed_ids = self_ids - other_ids
        common_ids = self_ids & other_ids

        result = SectionDiffResult()

        for aid in sorted(added_ids):
            result.added_sections.append(other_md.indexed_sections[aid])

        for rid in sorted(removed_ids):
            result.removed_sections.append(self.indexed_sections[rid])

        for cid in sorted(common_ids):
            old_sec = self.indexed_sections[cid]
            new_sec = other_md.indexed_sections[cid]

            if (old_sec.content.strip() != new_sec.content.strip()
                    or old_sec.level != new_sec.level or old_sec.ranks != new_sec.ranks):
                change = SectionChange(
                    section_id=cid,
                    title=new_sec.title,
                    old_content=old_sec.content,
                    new_content=new_sec.content,
                    old_ranks=old_sec.ranks,
                    new_ranks=new_sec.ranks,
                    old_level=old_sec.level,
                    new_level=new_sec.level
                )
                result.modified_sections.append(change)
            else:
                result.unchanged_sections.append(new_sec)

        return result

    def to_markdown(self, include_ranks: bool = True) -> str:
        """Serialize current state to Markdown string."""
        return self.document.to_markdown(include_ranks=include_ranks)

    def save(self, path: Path | str | None = None, include_ranks: bool = True) -> Path:
        """
        Save modified document back to disk.
        If path is None, saves back to original file_path.
        """
        target_path = Path(path).expanduser().resolve() if path else self.file_path
        if not target_path:
            raise ValueError("No output path specified and MDFile was not initialized with a file path.")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(self.to_markdown(include_ranks=include_ranks), encoding="utf-8")
        self.file_path = target_path
        return target_path
