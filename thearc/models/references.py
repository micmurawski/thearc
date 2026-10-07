"""Line-addressable files of a MetaAgent and resolution of ``path#Lx-Ly`` references.

A reflector reads the agent as ordinary files and cites line ranges. Ranks stay on
resources and Markdown sections: a reference resolves to the innermost one that
contains it. Files are rendered from the rank-free agent, so line numbers are
pinned to that configuration revision and rank metadata never shifts them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from thearc.models.markdown import MarkdownDocument

if TYPE_CHECKING:
    from thearc.models.agent import MetaAgent, ResourceTarget

REFERENCE = re.compile(r"(?P<path>[^#\s]+)#L(?P<start>[1-9][0-9]*)(?:-L(?P<end>[1-9][0-9]*))?")


@dataclass(frozen=True)
class FileSpan:
    """Lines ``start``..``end`` (1-based, inclusive) of ``path`` belong to ``target``."""

    path: str
    start: int
    end: int
    target: ResourceTarget
    depth: int

    @property
    def reference(self) -> str:
        return f"{self.path}#L{self.start}-L{self.end}"


@dataclass(frozen=True)
class AgentFiles:
    """The rank-free agent as files, with the resource or section that owns each line range."""

    files: dict[str, str]
    spans: tuple[FileSpan, ...] = field(default_factory=tuple)

    @classmethod
    def from_agent(cls, agent: MetaAgent) -> AgentFiles:
        from thearc.models.agent import ResourceTarget

        agent = agent.without_ranks()
        files: dict[str, str] = {}
        spans: list[FileSpan] = []
        addressable = {(t.kind, t.name, t.section): depth for t, depth in agent.iter_targets()}

        def add_document(path, target, text):
            if path in files:
                raise ValueError(f"Two resources map to the same file: {path}")
            files[path] = text
            spans.append(FileSpan(path, 1, max(1, len(text.splitlines())), target, 0))
            for start, end, _, title in MarkdownDocument.heading_spans(text):
                depth = addressable.get((target.kind, target.name, title))
                if depth is not None:  # Repeated headings have no address of their own.
                    spans.append(FileSpan(path, start, end, target.model_copy(update={"section": title}), depth))

        for name, skill in sorted(agent.skills.items()):
            add_document(f"skills/{name}/SKILL.md", ResourceTarget(kind="skill", name=name), skill.instructions)
            for relative, content in sorted(skill.files.items()):
                target = ResourceTarget(kind="skill_file", name=f"{name}/{relative}")
                if relative.endswith(".md"):
                    add_document(f"skills/{name}/{relative}", target, content)
                else:
                    path = f"skills/{name}/{relative}"
                    if path in files:
                        raise ValueError(f"Two resources map to the same file: {path}")
                    files[path] = content
                    spans.append(FileSpan(path, 1, max(1, len(content.splitlines())), target, 0))
        for name, document in sorted(agent.context.items()):
            add_document(f"context/{name}", ResourceTarget(kind="context", name=name), document.content)
        for item in sorted(agent.resources.values(), key=lambda r: (r.location, r.path)):
            add_document(f"{item.location}/{item.path}",
                         ResourceTarget(kind=item.location[:-1], name=item.path), item.content)
        for path, kind, entries in (
            ("hooks.json", "hook", {name: hook.to_dict(include_ranks=False) for name, hook in agent.hooks.items()}),
            ("mcp.json", "mcp", {name: server.to_dict() for name, server in agent.mcps.items()}),
        ):
            if entries:
                text, entry_spans = _json_entries(entries)
                files[path] = text
                spans += [FileSpan(path, start, end, ResourceTarget(kind=kind, name=name), 0)
                          for name, (start, end) in entry_spans.items()]
        return cls(files, tuple(spans))

    def line_count(self, path: str) -> int:
        return len(self._text(path).splitlines())

    def _text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError:
            raise ValueError(f"Unknown file: {path}. Use list_files to see the agent's files.") from None

    def read(self, path: str, start: int = 1, end: int | None = None) -> str:
        """Lines ``start``..``end`` prefixed with their numbers, for citing."""
        lines = self._text(path).splitlines()
        end = len(lines) if end is None else min(end, len(lines))
        if start < 1 or (lines and start > len(lines)) or end < start - 1:
            raise ValueError(f"{path} has {len(lines)} lines; requested L{start}-L{end}")
        width = len(str(max(end, 1)))
        return "\n".join(f"{number:>{width}} | {lines[number - 1]}" for number in range(start, end + 1))

    def outline(self, path: str) -> list[FileSpan]:
        self._text(path)
        return sorted((s for s in self.spans if s.path == path), key=lambda s: (s.start, -s.end))

    def resolve(self, reference: str) -> ResourceTarget:
        """The innermost resource or section containing ``path#Lx-Ly``.

        A range that only partly overlaps a section (for example one starting in
        Step 1 and ending in Step 2) is ambiguous and rejected, so attribution
        stays precise; cite within one section or the whole enclosing section.
        """
        match = REFERENCE.fullmatch(reference.strip())
        if match is None:
            raise ValueError(f"Invalid reference {reference!r}; use path#L14-L25 or path#L14")
        path = str(PurePosixPath(match["path"]))
        start = int(match["start"])
        end = int(match["end"] or start)
        total = self.line_count(path)
        if end < start or end > total:
            raise ValueError(f"{reference}: {path} has {total} lines; use a range within L1-L{total}")
        spans = self.outline(path)
        partial = [s for s in spans if s.start <= end and start <= s.end
                   and not (s.start <= start and end <= s.end) and not (start <= s.start and s.end <= end)]
        if partial:
            span = partial[0]
            name = span.target.section or span.target.name
            raise ValueError(f"{reference} crosses the boundary of {name!r} ({span.reference}); "
                             "cite lines within one section or the whole enclosing section")
        containing = [s for s in spans if s.start <= start and end <= s.end]
        if not containing:
            raise ValueError(f"{reference} is not inside one resource; cite lines of a single entry")
        return max(containing, key=lambda s: (s.depth, -(s.end - s.start))).target

    def reference_for(self, target: ResourceTarget) -> str:
        """Where a resource or section currently lives, e.g. ``skills/x/SKILL.md#L60-L107``."""
        for span in self.spans:
            if span.target == target:
                return span.reference
        raise ValueError(f"No file lines for {target}")

    def write(self, root: str | Path) -> Path:
        """Create ``root`` (which must not exist) and write every file into it."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=False)
        for relative, content in self.files.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        return root


def _json_entries(entries: dict) -> tuple[str, dict[str, tuple[int, int]]]:
    """Pretty JSON object whose top-level entries have known line ranges."""
    lines, spans = ["{"], {}
    names = sorted(entries)
    for position, name in enumerate(names):
        block = json.dumps({name: entries[name]}, ensure_ascii=False, indent=2).splitlines()[1:-1]
        if position < len(names) - 1:
            block[-1] += ","
        spans[name] = (len(lines) + 1, len(lines) + len(block))
        lines += block
    lines.append("}")
    return "\n".join(lines) + "\n", spans
