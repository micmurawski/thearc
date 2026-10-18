"""Markers and stamps: the two kinds of comment thearc writes into files, and removes when reading them.

Both are comments in the file's own syntax at the end of an existing line, so line numbers and
file references stay valid, and both are placed by the same rules:

    ## Limits <!-- … -->        Markdown: a heading line
    set -e  # …                 code file: the first line (the second after a shebang; never a line
                                that continues onto the next)
      "guard": {  // …          JSONC entry: the line of its key

**Markers** (workspace) hold a target's lineage ID, ``res_…``. While someone edits a workspace,
the marker says which section is which, so a renamed section keeps its history. Reading a
workspace strips them, so revisions, installations, and the files reflectors read never contain
them. Ratings are never in a marker (``agent_outline`` shows them); markers written before that
still read.

**Stamps** (installation) hold the installed agent's name and version, ``graphify 0.9.69``, so a
session transcript shows which version the harness read. A context document gets one on each
section thearc installed, other Markdown on its top-level headings (or on a line of its own when
it has none — the only case that adds a line), and code files on their first line. Claude Code
drops HTML comments on their own line in ``CLAUDE.md`` but keeps one on a heading, hence headings.
Stamps change on release, not on every curation. Importing an installation removes them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from thearc.resources.files import AgentFiles
from thearc.resources.markdown import find_section, outline, split_frontmatter
from thearc.resources.targets import ResourceTarget

# --- comment syntax and placement (shared) -------------------------------------------------------------------------

_LINE_COMMENTS = {
    "#": (".py", ".sh", ".bash", ".zsh", ".fish", ".rb", ".pl", ".ps1", ".r", ".yaml", ".yml", ".toml", ".conf"),
    "//": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp",
           ".hpp", ".cs", ".swift", ".scala", ".dart"),
    "--": (".sql", ".lua", ".hs"),
    ";": (".lisp", ".clj", ".el", ".ini"),
}
_COMMENT = {suffix: comment for comment, suffixes in _LINE_COMMENTS.items() for suffix in suffixes}


def code_comment(path: str) -> str | None:
    """The line-comment syntax of a code file, or None for Markdown, JSON, and unknown file types."""
    return _COMMENT.get(PurePosixPath(path).suffix.lower())


def _kind(path: str) -> str | None:
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".json"):
        return "json"
    return "code" if code_comment(path) else None


def _comment(path: str, payload: str) -> str:
    """``payload`` as a trailing comment for a line of ``path``, with its leading gap."""
    kind = _kind(path)
    if kind == "markdown":
        return f" <!-- {payload} -->"
    return f"  {'//' if kind == 'json' else code_comment(path)} {payload}"


def _trailing(path: str, payload: str) -> re.Pattern[str]:
    """A line ending in a comment whose payload matches the regex ``payload``; ``text`` is the line without it."""
    kind = _kind(path)
    if kind == "markdown":
        return re.compile(rf"^(?P<text>.*?)[ \t]*<!-- (?P<payload>{payload}) -->[ \t]*$")
    comment = "//" if kind == "json" else re.escape(code_comment(path) or "#")
    return re.compile(rf"^(?P<text>.*?)[ \t]*{comment} (?P<payload>{payload})$")


def code_line(text: str) -> int | None:
    """0-based line of a code file's comment: the first, or the second after a shebang; None when there is no
    such line or it continues onto the next (a comment there would break the continuation)."""
    lines = text.splitlines()
    index = 1 if text.startswith("#!") else 0
    if index >= len(lines) or lines[index].rstrip().endswith("\\"):
        return None
    return index


def _append(text: str, additions: Mapping[int, str]) -> str:
    """``text`` with a trailing comment added to each 0-based line in ``additions``."""
    lines = text.splitlines(keepends=True)
    for index, comment in additions.items():
        content = lines[index].rstrip("\r\n")
        lines[index] = (f"{content}{comment}" if content.strip() else comment.lstrip()) + lines[index][len(content):]
    return "".join(lines)


# --- workspace markers: lineage IDs --------------------------------------------------------------------------------

_ID = r"res_[0-9a-f]{24}"
_MARKER = rf"(?P<id>{_ID})(?: · [^>]*?)?"  # `` · ratings`` after the ID: markers written before ratings left them.


def _marker_line(files: AgentFiles, target: ResourceTarget) -> tuple[str, int] | None:
    """Where ``target``'s marker goes: (path, 0-based line), or None for targets without one."""
    span = files.span(target)
    kind = _kind(span.path)
    if target.section is not None or kind == "json":
        return span.path, span.start - 1
    if kind == "code" and span.depth == 0 and (index := code_line(files.files[span.path])) is not None:
        return span.path, index
    return None  # A whole Markdown resource: its ID appears in the outline.


def annotate(files: AgentFiles, ids: Mapping[ResourceTarget, str]) -> dict[str, str]:
    """``files`` with a marker holding each target's lineage ID (``res_…``)."""
    additions: dict[str, dict[int, str]] = {}
    for target, identifier in ids.items():
        if (place := _marker_line(files, target)) is not None:
            additions.setdefault(place[0], {})[place[1]] = _comment(place[0], identifier)
    return {path: _append(text, additions[path]) if path in additions else text for path, text in files.files.items()}


def strip(path: str, text: str) -> tuple[str, dict[int, str]]:
    """``text`` without markers, and the marker IDs found, by 1-based line."""
    kind = _kind(path)
    if kind is None:
        return text, {}
    pattern = _trailing(path, _MARKER)
    lines = text.splitlines(keepends=True)
    found: dict[int, str] = {}
    for index, line in enumerate(lines):
        if kind == "code" and index > 1:
            break
        body = line.rstrip("\r\n")
        match = pattern.match(body)
        if match:
            if pattern.match(match["text"]):
                raise ValueError(f"{path}:{index + 1}: two markers on one line; a target has one ID")
            found[index + 1] = match["id"]
            lines[index] = match["text"] + line[len(body) :]
    return "".join(lines), found


def identities(files: AgentFiles, found: Mapping[str, Mapping[int, str]]) -> dict[ResourceTarget, str]:
    """Which target each marker ID belongs to, given the markers ``strip`` found in each file of ``files``."""
    result: dict[ResourceTarget, str] = {}
    seen: dict[str, ResourceTarget] = {}
    for target in files.targets:
        place = _marker_line(files, target)
        if place is None:
            continue
        identifier = found.get(place[0], {}).get(place[1] + 1)
        if identifier is None:
            continue
        if identifier in seen:
            raise ValueError(f"marker {identifier} appears on both {seen[identifier]} and {target}")
        seen[identifier] = target
        result[target] = identifier
    return result


# --- install stamps: name and version ------------------------------------------------------------------------------


def label(name: str, version: str | None) -> str | None:
    """The stamp text, ``graphify 0.9.69``; None for an agent without a version (nothing is stamped)."""
    return f"{name} {version}" if version else None


def _stamp_of(name: str) -> str:
    return rf"{re.escape(name)} [^\s>]+"


def stamp_markdown(text: str, stamp: str, *, titles: set[str] | None = None) -> str:
    """``text`` with ``stamp`` on the headings of its top-level sections, or — without any heading — on a
    line of its own at the top of the body. With ``titles``, only the sections a context merge matched
    by those titles (``replace_sections``) are stamped, at whatever level they sit in the document."""
    _, body, skipped = split_frontmatter(text)
    headings = outline(body, first_line=skipped + 1)
    if titles is not None:
        starts = {heading.start for title in titles if (heading := find_section(text, title)) is not None}
    elif headings:
        level = min(heading.level for heading in headings)
        starts = {heading.start for heading in headings if heading.level == level}
    else:
        lines = text.splitlines(keepends=True)
        return "".join(lines[:skipped]) + f"<!-- {stamp} -->\n" + body if body.strip() else text
    return _append(text, {start - 1: _comment(".md", stamp) for start in starts})


def stamp_file(path: str, text: str, stamp: str) -> str:
    """``text`` with ``stamp`` in a comment of the file's own syntax; unchanged for other file types."""
    kind = _kind(path)
    if kind == "markdown":
        return stamp_markdown(text, stamp) if text.strip() else text
    if kind == "code" and (index := code_line(text)) is not None:
        return _append(text, {index: _comment(path, stamp)})
    return text


def unstamp_file(path: str, text: str, name: str) -> str:
    """``text`` without ``name``'s stamps; a stamp on a line of its own goes with its line."""
    kind = _kind(path)
    if kind not in {"markdown", "code"}:
        return text
    pattern = _trailing(path, _stamp_of(name))
    result = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        content = line.rstrip("\r\n")
        match = pattern.match(content) if kind == "markdown" or index < 2 else None
        if match is None:
            result.append(line)
        elif match["text"].strip() or kind == "code":
            result.append(match["text"] + line[len(content) :])
    return "".join(result)


def unstamp_markdown(text: str, name: str) -> str:
    return unstamp_file(".md", text, name)

