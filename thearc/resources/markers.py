"""Markers: target IDs and rating summaries written into workspace files, and stripped when read.

A marker sits at the end of the line that starts its target, so annotated and
clean files have the same lines:

    ## Limits <!-- res_fd94… · usefulness: (harmful: 3, neutral: 0, helpful: 1) -->   Markdown section heading
    echo hi  # res_0eeb… · usefulness: (helpful: 1)                                  code file, first line
      "guard": {  // res_8c1f…                                                      hooks / MCP entry (JSONC)

The ID is the target's lineage ID, so a section keeps it through renames: while
someone edits the workspace, the marker is what says which section is which.
Ratings are a copy of the journal's summaries as of the last refresh. Markers are
never part of the configuration: reading a workspace strips them, so revisions,
installations, and the files reflectors read never contain them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from thearc.resources.files import AgentFiles
from thearc.resources.targets import ResourceTarget

_ID = r"res_[0-9a-f]{24}"
_MARKDOWN = re.compile(rf"^(?P<text>.*?)[ \t]*<!-- (?P<id>{_ID})(?: · .*?)? -->[ \t]*$")
_JSON = re.compile(rf"^(?P<text>.*?)[ \t]*// (?P<id>{_ID})(?: · .*)?$")
_CODE = re.compile(rf"^(?P<text>.*?)[ \t]*(?:#|//|--|;) (?P<id>{_ID})(?: · .*)?$")

# A code file has no heading, so its marker is a comment in the file's own syntax.
_LINE_COMMENTS = {
    "#": (".py", ".sh", ".bash", ".zsh", ".fish", ".rb", ".pl", ".ps1", ".r", ".yaml", ".yml", ".toml", ".conf"),
    "//": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp",
           ".hpp", ".cs", ".swift", ".scala", ".dart"),
    "--": (".sql", ".lua", ".hs"),
    ";": (".lisp", ".clj", ".el", ".ini"),
}
_COMMENT = {suffix: comment for comment, suffixes in _LINE_COMMENTS.items() for suffix in suffixes}


def code_comment(path: str) -> str | None:
    """The line-comment marker for a code file, or None for Markdown, JSON, and unknown file types."""
    return _COMMENT.get(PurePosixPath(path).suffix.lower())


def _kind(path: str) -> str | None:
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".json"):
        return "json"
    return "code" if code_comment(path) else None


def _marker_line(files: AgentFiles, target: ResourceTarget) -> tuple[str, int] | None:
    """Where ``target``'s marker goes: (path, 1-based line), or None for targets without one."""
    span = files.span(target)
    kind = _kind(span.path)
    if target.section is not None or kind == "json":
        return span.path, span.start
    if kind == "code" and span.depth == 0:
        shebang = files.files[span.path].startswith("#!")
        line = 2 if shebang else 1
        return (span.path, line) if span.end >= line else None
    return None  # A whole Markdown resource: its ID and ratings appear in the outline.


def annotate(files: AgentFiles, labels: Mapping[ResourceTarget, str]) -> dict[str, str]:
    """``files`` with a marker for every labelled target; a label is ``res_…`` optionally followed by `` · ratings``."""
    additions: dict[str, dict[int, str]] = {}
    for target, label in labels.items():
        place = _marker_line(files, target)
        if place is None:
            continue
        path, line = place
        kind = _kind(path)
        if kind == "markdown":
            marker = f" <!-- {label} -->"
        elif kind == "json":
            marker = f"  // {label}"
        else:
            marker = f"  {code_comment(path)} {label}"
        additions.setdefault(path, {})[line - 1] = marker
    result = dict(files.files)
    for path, markers in additions.items():
        lines = result[path].splitlines(keepends=True)
        for index, marker in markers.items():
            text = lines[index].rstrip("\r\n")
            ending = lines[index][len(text) :]
            lines[index] = (f"{text}{marker}" if text.strip() else marker.lstrip()) + ending
        result[path] = "".join(lines)
    return result


def strip(path: str, text: str) -> tuple[str, dict[int, str]]:
    """``text`` without markers, and the marker IDs found, by 1-based line."""
    kind = _kind(path)
    if kind is None:
        return text, {}
    pattern = {"markdown": _MARKDOWN, "json": _JSON, "code": _CODE}[kind]
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
        identifier = found.get(place[0], {}).get(place[1])
        if identifier is None:
            continue
        if identifier in seen:
            raise ValueError(f"marker {identifier} appears on both {seen[identifier]} and {target}")
        seen[identifier] = target
        result[target] = identifier
    return result
