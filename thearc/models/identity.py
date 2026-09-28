"""Release identity metadata for installed MetaAgent configurations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

IDENTITY_FILE = ".thearc-agent.json"


def identity_path(base: Path) -> Path:
    path = base / IDENTITY_FILE
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("MetaAgent identity paths must not contain symlinks")
    return path


def version_filename(name: str) -> str:
    if (not isinstance(name, str) or not name.strip() or name in {".", ".."}
            or any(char in name for char in "/\\:") or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError("MetaAgent name must be a safe filename component")
    return f".{name}_version"


def version_path(base: Path, name: str) -> Path:
    path = base / version_filename(name)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("MetaAgent version paths must not contain symlinks")
    if path.exists() and not path.is_file():
        raise ValueError("MetaAgent version marker must be a regular file")
    return path


def _read_version(path: Path) -> str:
    with path.open("rb") as stream:
        content = stream.read(16_385)
    if len(content) > 16_384:
        raise ValueError("MetaAgent version marker is too large")
    value = content.decode("utf-8").strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid MetaAgent version marker")
    return value


def read_identity(base: Path, *, name: str | None = None) -> dict:
    path = identity_path(base)
    if not path.exists():
        if name is not None:
            marker = version_path(base, name)
            return {"name": name, "version": _read_version(marker)} if marker.exists() else {}
        markers = sorted(base.glob(".*_version"))
        if len(markers) > 1:
            raise ValueError("Multiple MetaAgent version markers; specify name explicitly")
        if markers:
            marker_name = markers[0].name[1:-len("_version")]
            marker = version_path(base, marker_name)
            return {"name": marker_name, "version": _read_version(marker)}
        return {}
    if not path.is_file():
        raise ValueError("MetaAgent identity must be a regular file")
    with path.open("rb") as stream:
        content = stream.read(16_385)
    if len(content) > 16_384:
        raise ValueError("MetaAgent identity file is too large")
    data = json.loads(content)
    if (not isinstance(data, dict) or set(data) != {"schema_version", "name", "version"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Invalid MetaAgent identity file")
    marker = version_path(base, data["name"])
    if marker.exists() and _read_version(marker) != data["version"]:
        raise ValueError("MetaAgent version marker disagrees with identity metadata")
    return {"name": data["name"], "version": data["version"]}


def write_identity(path: Path, name: str, version: str | None) -> list[Path]:
    """Write identity and a named marker; the pair/full install is not atomic."""
    path = identity_path(path.parent)
    marker = version_path(path.parent, name)
    # Only remove old markers explicitly owned by an existing identity sidecar.
    previous = read_identity(path.parent) if path.exists() else {}
    old_marker = version_path(path.parent, previous["name"]) if previous else None
    content = json.dumps({"schema_version": 1, "name": name, "version": version}, ensure_ascii=False) + "\n"
    if len(content.encode("utf-8")) > 16_384:
        raise ValueError("MetaAgent identity is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    if version is not None:
        _write_text(marker, version + "\n")
    elif marker.exists():
        marker.unlink()
    if old_marker is not None and old_marker != marker and old_marker.exists():
        old_marker.unlink()
    _write_text(path, content)
    return [path, marker] if version is not None else [path]


def _write_text(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".thearc-identity-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
