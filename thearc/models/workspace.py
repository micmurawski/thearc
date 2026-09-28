"""Canonical, editable MetaAgent files, independent of any installed harness.

The manifest holds structured fields and explicit references to exact UTF-8 text.
It is local configuration, not a redacted or sandboxed model input. No hooks,
scripts, instructions, or MCPs are executed by these functions.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from thearc.models.agent import MetaAgent
from thearc.models.identity import version_filename

MANIFEST = "agent.json"
SCHEMA_VERSION = 1


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or any(part in {".", "..", ""} for part in value.split("/"))
            or ":" in value or "\x00" in value):
        raise ValueError(f"Unsafe workspace path: {value!r}")
    return value


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("Workspace paths must not contain symlinks")
    return root


def _text_slots(data: dict):
    """Yield mutable containers/keys and default file paths for all external text."""
    for name, skill in data.get("skills", {}).items():
        # Validate identities separately, not just the composed output paths.
        name = _relative(name)
        if "/" in name:
            raise ValueError("Workspace skill names must be single path components")
        yield skill, "instructions", f"skills/{name}/instructions.md"
        for relative in skill.get("files", {}):
            # Bundled files have their own subtree to avoid instructions collisions.
            yield skill["files"], relative, f"skills/{name}/files/{_relative(relative)}"
    for name, document in data.get("context", {}).items():
        yield document, "content", f"context/{_relative(name)}"
    for resource in data.get("resources", {}).values():
        yield resource, "content", f"resources/{_relative(resource['location'])}/{_relative(resource['path'])}"


def _workspace_files(agent: MetaAgent) -> dict[str, str]:
    """Render canonical files in memory, sharing export validation with diffs."""
    data = agent.model_dump(mode="json")
    files = {}
    if agent.version is not None:
        files[version_filename(agent.name)] = agent.version + "\n"
    for container, key, relative in _text_slots(data):
        relative = _relative(relative)
        if relative in files:
            raise ValueError(f"Duplicate workspace file: {relative}")
        files[relative] = container[key]
        container[key] = {"file": relative}
    # Detect file/directory collisions before any filesystem writes.
    for relative in files:
        if any(parent.as_posix() in files for parent in PurePosixPath(relative).parents):
            raise ValueError(f"Workspace file/directory collision: {relative}")
    serialized = json.dumps({"schema_version": SCHEMA_VERSION, "agent": data}, indent=2, ensure_ascii=False)
    files[MANIFEST] = serialized + "\n"
    return files


def write_workspace(agent: MetaAgent, path: str | Path) -> Path:
    """Export to a new directory only. Existing workspace files are never replaced."""
    root = _root(path)
    files = _workspace_files(agent)
    root.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
    return root


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def read_workspace(path: str | Path, *, max_bytes: int = 10_000_000, max_files: int = 10_000) -> MetaAgent:
    """Import explicit manifest files; reject orphan files and symlinks.

    Add/remove text resources by changing both files and their manifest entries.
    This reader validates configuration I/O, not concurrent hostile filesystem
    mutation. A future curator runner must isolate writes before import.
    """
    if max_bytes < 1 or max_files < 1:
        raise ValueError("Workspace limits must be positive")
    root = _root(path)
    if not root.is_dir():
        raise ValueError("Workspace must be an existing directory")
    actual = set()
    for nodes, item in enumerate(root.rglob("*"), start=1):
        if nodes > max_files:
            raise ValueError("Workspace exceeds entry limit")
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError("Workspace contains a symlink or non-regular file")
        if item.is_file():
            actual.add(item.relative_to(root).as_posix())
    remaining = max_bytes

    def read(relative):
        nonlocal remaining
        relative = _relative(relative)
        if relative not in actual:
            raise ValueError(f"Missing workspace file: {relative}")
        with (root / relative).open("rb") as stream:
            content = stream.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError("Workspace exceeds byte limit")
        return content.decode("utf-8")

    manifest = json.loads(read(MANIFEST), object_pairs_hook=_unique_object)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "agent"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION):
        raise ValueError("Unsupported workspace manifest")
    data = manifest["agent"]
    if not isinstance(data, dict):
        raise ValueError("Invalid workspace agent")  # noqa: TRY004 -- malformed serialized input
    referenced = {MANIFEST}
    identity = MetaAgent.model_validate({key: data[key] for key in ("name", "version") if key in data})
    marker = version_filename(identity.name)
    if marker in actual:
        if identity.version is None or read(marker).strip() != identity.version:
            raise ValueError("MetaAgent version marker disagrees with workspace metadata")
        referenced.add(marker)
    try:
        for container, key, _ in _text_slots(data):
            reference = container[key]
            if not isinstance(reference, dict) or set(reference) != {"file"} or not isinstance(reference["file"], str):
                raise ValueError("Text fields require an explicit file reference")
            relative = _relative(reference["file"])
            if relative in referenced:
                raise ValueError(f"Duplicate workspace file reference: {relative}")
            referenced.add(relative)
            container[key] = read(relative)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed workspace resource mapping") from exc
    if actual != referenced:
        raise ValueError(f"Untracked workspace files: {sorted(actual - referenced)}")
    return MetaAgent.model_validate(data)
