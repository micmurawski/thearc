"""Agent files: the configuration as line-addressable files, and file-reference resolution.

The layout is the workspace layout, so a reference such as
``skills/retry/SKILL.md#L12-L30`` names the same lines a reflector read, a human
edits, and git diffs:

    skills/<name>/SKILL.md      frontmatter (name, description, metadata) + instructions
    skills/<name>/<path>        bundled skill files
    context/<filename>          context documents
    rules|workflows|commands/<path>
    hooks.json, mcp.json        one top-level entry per hook / MCP server

Every line is owned by the innermost target containing it: a Markdown section,
or else the whole resource.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from thearcv2.resources.markdown import outline, render_frontmatter, split_frontmatter
from thearcv2.resources.targets import Kind, ResourceTarget

if TYPE_CHECKING:
    from thearcv2.resources.agent import MetaAgent

HOOKS_FILE = "hooks.json"
MCP_FILE = "mcp.json"
_REFERENCE = re.compile(r"(?P<path>[^#\s]+)#L(?P<start>[1-9][0-9]*)(?:-L(?P<end>[1-9][0-9]*))?")


@dataclass(frozen=True)
class FileReference:
    """``path#Lx`` or ``path#Lx-Ly``: 1-based, inclusive lines of an agent file."""

    path: str
    start: int
    end: int

    @classmethod
    def parse(cls, reference: str) -> FileReference:
        match = _REFERENCE.fullmatch(reference.strip())
        if match is None:
            raise ValueError(f"invalid file reference {reference!r}; use path#Lx or path#Lx-Ly")
        start = int(match["start"])
        end = int(match["end"] or start)
        if end < start:
            raise ValueError(f"invalid file reference {reference!r}: range ends before it starts")
        return cls(match["path"], start, end)

    def __str__(self) -> str:
        return f"{self.path}#L{self.start}" + (f"-L{self.end}" if self.end != self.start else "")


@dataclass(frozen=True)
class Span:
    """Lines ``start``..``end`` of ``path`` hold ``target``; ``depth`` 0 is a whole resource."""

    target: ResourceTarget
    path: str
    start: int
    end: int
    depth: int

    @property
    def reference(self) -> FileReference:
        return FileReference(self.path, self.start, max(self.start, self.end))


def _json_entries(entries: dict[str, dict]) -> tuple[str, dict[str, tuple[int, int]]]:
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


def skill_markdown(name: str, description: str, metadata: dict, instructions: str) -> str:
    frontmatter = {"name": name, **({"description": description} if description else {}), **metadata}
    return render_frontmatter(frontmatter, instructions)


def structured_entry(model: BaseModel) -> dict[str, Any]:
    """The JSON form of a hook or MCP inside ``hooks.json`` / ``mcp.json``."""
    return model.model_dump(mode="json", exclude={"name"}, exclude_defaults=True)


class AgentFiles:
    """Rendered files of one configuration revision, with the target owning each line."""

    def __init__(self, files: Mapping[str, str], spans: list[Span]):
        self.files: Mapping[str, str] = MappingProxyType(dict(files))
        self.spans: tuple[Span, ...] = tuple(spans)
        self._owners: dict[str, list[ResourceTarget | None]] = {}
        for path, text in self.files.items():
            owners: list[ResourceTarget | None] = [None] * len(text.splitlines())
            for span in sorted((s for s in self.spans if s.path == path), key=lambda s: s.depth):
                owners[span.start - 1:span.end] = [span.target] * (span.end - span.start + 1)
            self._owners[path] = owners
        self._by_target = {span.target: span for span in self.spans}

    @classmethod
    def from_agent(cls, agent: MetaAgent) -> AgentFiles:
        files: dict[str, str] = {}
        spans: list[Span] = []

        def add(path: str, text: str, target: ResourceTarget, *, markdown: bool) -> None:
            if path in files:
                raise ValueError(f"two resources map to the same file: {path}")
            files[path] = text
            spans.append(Span(target, path, 1, len(text.splitlines()), 0))
            if not markdown:
                return
            _, body, offset = split_frontmatter(text)
            headings = outline(body, first_line=offset + 1)
            repeated = Counter(heading.address for heading in headings)
            for heading in headings:
                if repeated[heading.address] == 1:  # A repeated heading path has no address of its own.
                    section = target.model_copy(update={"section": heading.address})
                    spans.append(Span(section, path, heading.start, heading.end, len(heading.path)))

        for name, skill in sorted(agent.skills.items()):
            text = skill_markdown(name, skill.description, skill.metadata, skill.instructions)
            add(f"skills/{name}/SKILL.md", text, ResourceTarget(kind="skill", name=name), markdown=True)
            for relative, content in sorted(skill.files.items()):
                target = ResourceTarget(kind="skill_file", name=f"{name}/{relative}")
                add(f"skills/{name}/{relative}", content, target, markdown=relative.endswith(".md"))
        for filename, document in sorted(agent.context.items()):
            add(f"context/{filename}", document.content, ResourceTarget(kind="context", name=filename), markdown=True)
        for key, item in sorted(agent.resources.items()):
            add(key, item.content, ResourceTarget(kind=item.kind, name=item.path), markdown=True)
        structured: list[tuple[str, Kind, dict[str, Any]]] = [
            (HOOKS_FILE, "hook", dict(agent.hooks)), (MCP_FILE, "mcp", dict(agent.mcps))]
        for path, kind, collection in structured:
            if collection:
                text, entry_spans = _json_entries({name: structured_entry(item) for name, item in collection.items()})
                files[path] = text
                spans += [Span(ResourceTarget(kind=kind, name=name), path, start, end, 0)
                          for name, (start, end) in entry_spans.items()]
        for path in files:
            if any(parent.as_posix() in files for parent in PurePosixPath(path).parents):
                raise ValueError(f"file/directory collision: {path}")
        return cls(dict(sorted(files.items())), spans)

    @property
    def revision(self) -> str:
        """Content hash of all files; line numbers in references are pinned to it."""
        digest = hashlib.sha256()
        for path, text in sorted(self.files.items()):
            digest.update(json.dumps([path, text], ensure_ascii=False).encode())
        return "rev_" + digest.hexdigest()[:24]

    @property
    def targets(self) -> list[ResourceTarget]:
        """Every addressable target, each resource followed by its sections in document order."""
        return [span.target for span in sorted(self.spans, key=lambda s: (s.path, s.start, s.depth))]

    def span(self, target: ResourceTarget) -> Span:
        try:
            return self._by_target[target]
        except KeyError:
            raise KeyError(f"no lines for {target}") from None

    def _lines(self, path: str) -> list[str]:
        try:
            return self.files[path].splitlines()
        except KeyError:
            raise ValueError(f"unknown file: {path}") from None

    def read(self, path: str, start: int = 1, end: int | None = None) -> str:
        """Lines ``start``..``end`` prefixed with their numbers, for citing."""
        lines = self._lines(path)
        end = len(lines) if end is None else min(end, len(lines))
        if start < 1 or (lines and start > len(lines)) or end < start - 1:
            raise ValueError(f"{path} has {len(lines)} lines; requested L{start}-L{end}")
        width = len(str(max(end, 1)))
        return "\n".join(f"{number:>{width}} | {lines[number - 1]}" for number in range(start, end + 1))

    def resolve(self, reference: str | FileReference) -> tuple[ResourceTarget, ...]:
        """Every innermost target the referenced lines overlap, in line order."""
        if isinstance(reference, str):
            reference = FileReference.parse(reference)
        total = len(self._lines(reference.path))
        if reference.end > total:
            raise ValueError(f"{reference}: {reference.path} has {total} lines")
        owners = self._owners[reference.path][reference.start - 1:reference.end]
        targets = tuple(dict.fromkeys(owner for owner in owners if owner is not None))
        if not targets:
            raise ValueError(f"{reference} does not cover any resource")
        return targets

    def owned_text(self, target: ResourceTarget) -> str:
        """The lines a target owns, without a section's own heading (its title is its address)."""
        span = self.span(target)
        lines = self._lines(span.path)
        owned = [line for number, line in enumerate(lines, start=1)
                 if self._owners[span.path][number - 1] == target]
        if target.section is not None:
            owned = owned[1:]
        return "\n".join(owned).strip().rstrip(",")

    def content_hash(self, target: ResourceTarget) -> str:
        return "sha256:" + hashlib.sha256(self.owned_text(target).encode()).hexdigest()[:24]

    def __iter__(self) -> Iterator[str]:
        return iter(self.files)

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


def diff_files(before: Mapping[str, str], after: Mapping[str, str], *, context_lines: int = 3) -> str:
    """Git-style unified diff between two file mappings."""
    if isinstance(context_lines, bool) or not isinstance(context_lines, int) or context_lines < 0:
        raise ValueError("context_lines must be a non-negative integer")
    output = []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        source = f"a/{path}" if path in before else "/dev/null"
        destination = f"b/{path}" if path in after else "/dev/null"
        output.append(f"diff --git a/{path} b/{path}\n")
        lines = list(unified_diff(before.get(path, "").splitlines(keepends=True),
                                  after.get(path, "").splitlines(keepends=True),
                                  fromfile=source, tofile=destination, n=context_lines))
        if not lines:  # Adding or removing an empty file still matters.
            output.append(f"--- {source}\n+++ {destination}\n")
        for line in lines:
            output.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(output)
