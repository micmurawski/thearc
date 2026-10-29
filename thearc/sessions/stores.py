"""Harness session stores: where each harness keeps the sessions run in a project, as sources for the index.

- Claude: ``~/.claude/projects/<cwd, non-alphanumerics as ->/``, one folder per working directory: the
  project's folder and its subfolders' are registered.
- Pi: ``~/.pi/agent/sessions/--<cwd, / as ->--/``, the same way.
- Codex: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``, one store for every project: all of it is registered.
- OpenCode: a database, read through ``opencode session export``: the project's sessions are exported.

``Sessions.list(cwd=...)`` then keeps the sessions run in the project or below it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .install import default_home


def _mangled(path: Path, harness: str) -> str:
    if harness == "claude":
        return re.sub(r"[^A-Za-z0-9]", "-", str(path))
    return "--" + str(path).strip("/").replace("/", "-")  # Pi: --Users-me-project--


def store_folders(harness: str, project: str | Path, *, home: str | Path | None = None) -> list[Path]:
    """The folders of ``harness``'s native store that hold sessions run in ``project`` (or may: Codex keeps one
    store for every project). OpenCode keeps a database; see ``export_opencode_sessions``."""
    project = Path(project).resolve()
    root = Path(home).expanduser() if home else default_home(harness)
    if harness == "codex":
        return [root / "sessions"] if (root / "sessions").is_dir() else []
    if harness not in {"claude", "pi"}:
        raise ValueError(f"no native session store for {harness!r}; OpenCode sessions are exported")
    store = root / ("projects" if harness == "claude" else "sessions")
    if not store.is_dir():
        return []
    name = _mangled(project, harness)
    stem = name.removesuffix("--") if harness == "pi" else name
    return sorted(folder for folder in store.iterdir() if folder.is_dir()
                  and (folder.name == name or folder.name.startswith(stem + "-")))


def export_opencode_sessions(project: str | Path, folder: str | Path, *, binary: str | None = None,
                             limit: int = 200) -> list[Path]:
    """Export the OpenCode sessions run in ``project`` into ``folder`` (one ``<id>.jsonl`` each, as the index reads
    them), skipping those exported since they last changed; returns every export there."""
    project, folder = Path(project).resolve(), Path(folder)
    executable = binary or shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    folder.mkdir(parents=True, exist_ok=True)
    listed = subprocess.run([executable, "session", "list", "--format", "json", "-n", str(limit)], cwd=project,
                            capture_output=True, text=True, check=True).stdout
    for session in json.loads(listed or "[]"):
        directory = Path(session.get("directory") or "/").resolve()
        if directory != project and project not in directory.parents:
            continue
        target = folder / f"{session['id']}.jsonl"
        stamp = folder / f".{session['id']}.updated"
        if target.is_file() and stamp.is_file() and stamp.read_text() == str(session.get("updated")):
            continue
        exported = subprocess.run([executable, "session", "export", "--standalone", session["id"]], cwd=project,
                                  capture_output=True, text=True, check=True).stdout
        target.write_text(json.dumps(json.loads(exported), ensure_ascii=False) + "\n", encoding="utf-8")
        stamp.write_text(str(session.get("updated")))
    return sorted(folder.glob("*.jsonl"))


def within(path: str | Path, project: str | Path) -> bool:
    """Whether ``path`` is ``project`` or inside it."""
    path, project = Path(path).resolve(), Path(project).resolve()
    return path == project or project in path.parents

