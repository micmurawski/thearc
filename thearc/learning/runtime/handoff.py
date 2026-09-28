"""Offline handoff preparation. No runtime, configuration installation, or replay."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from thearc.learning.evidence.exports import render_context
from thearc.learning.evidence.snapshot import (
    EvidenceSnapshot,
    canonical_json,
    check_destination,
    content_hash,
    write_json_exclusive,
)
from thearc.learning.privacy import hide_historical_ranks, redact
from thearc.models.agent import MetaAgent


class HandoffMode(str, Enum):
    CONTEXT = "context"
    FORK = "fork"
    RESUME = "resume"
    IMPORT = "import"

    def __str__(self) -> str:
        return self.value


def _workspace_identity(workspace: str | Path) -> dict:
    path = Path(workspace).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Workspace must be a directory")

    def git(*args):
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(path), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode:
            raise ValueError("Workspace must be an existing Git worktree")
        return result.stdout.strip()

    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    if root != path:
        raise ValueError("Specify the workspace root, not a subdirectory")
    return {"path": str(path), "head": git("rev-parse", "HEAD"),
            "detached": not bool(git("branch", "--show-current")),
            "clean": not bool(git("status", "--porcelain", "--untracked-files=all")),
            "state": "observed_current_worktree",
            "checkpoint": False}


@dataclass(frozen=True, slots=True)
class HandoffPlan:
    """Immutable offline intent; a saved plan is never execution authorization."""

    evidence: EvidenceSnapshot = field(repr=False)
    _data: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.evidence, EvidenceSnapshot) or not isinstance(self._data, str):
            raise TypeError("Handoff plans require immutable evidence and serialized metadata")
        data = self.data
        if data.get("schema_version") != 1 or data.get("mode") != HandoffMode.CONTEXT:
            raise ValueError("Unsupported handoff schema or mode")
        if data.get("delivery") != "inline" or data.get("target") != "codex":
            raise ValueError("Only offline Codex context plans are supported")
        if not isinstance(data.get("task"), str) or not data["task"].strip():
            raise ValueError("A nonempty current task is required")
        if data.get("snapshot_sha256") != self.evidence.sha256:
            raise ValueError("Handoff evidence hash mismatch")
        context = render_context(self.evidence, max_chars=data["max_context_chars"])
        if data.get("coverage") != {"included": list(context.included_event_ids),
                                    "omitted": list(context.omitted_event_ids)}:
            raise ValueError("Handoff coverage mismatch")
        if data.get("config_sha256") != content_hash(data.get("destination_config")):
            raise ValueError("Handoff configuration hash mismatch")

    @property
    def data(self) -> dict:
        return json.loads(self._data)

    @property
    def sha256(self) -> str:
        return content_hash(self.data)

    def preview(self) -> dict:
        """Exact task/context payload for review, not a native thread import."""
        return {"task": self.data["task"],
                "historical_evidence": render_context(
                    self.evidence, max_chars=self.data["max_context_chars"]
                ).text,
                "destination_config": self.data["destination_config"]}


def prepare_handoff(evidence: EvidenceSnapshot, *, task: str, workspace: str | Path,
                    target: str = "codex", mode: HandoffMode = HandoffMode.CONTEXT,
                    destination_config: MetaAgent | None = None,
                    max_context_chars: int = 20_000) -> HandoffPlan:
    """Prepare an inert context artifact against an existing workspace; never create or modify it."""
    mode = HandoffMode(mode)
    if target != "codex" or mode != HandoffMode.CONTEXT:
        raise ValueError("Only context preparation for Codex is available; native runtime modes are not enabled")
    if not evidence.manifest["policy"]["redact"] or not evidence.manifest["policy"]["hide_ranks"]:
        raise ValueError("Handoff preparation requires redacted, rank-free evidence")
    context = render_context(evidence, max_chars=max_context_chars)
    config = redact(hide_historical_ranks(destination_config.without_ranks().model_dump(mode="json"))) \
        if destination_config is not None else None
    data = {"schema_version": 1, "mode": mode.value, "delivery": "inline", "target": target,
            "task": task, "snapshot_sha256": evidence.sha256,
            "session_ids": [s["id"] for s in evidence.sessions],
            "max_context_chars": max_context_chars,
            "coverage": {"included": list(context.included_event_ids), "omitted": list(context.omitted_event_ids)},
            "destination_config": config, "config_sha256": content_hash(config),
            "workspace": _workspace_identity(workspace),
            "permissions": {"execution_enabled": False, "configuration_installation": False},
            "limitations": ["No model is invoked and no configuration is installed.",
                            "Conversation evidence does not restore filesystem state.",
                            "Workspace identity is not a content checkpoint or execution preflight.",
                            "Only selected sanitized context is transferred; no native history or hidden state."]}
    return HandoffPlan(evidence, canonical_json(data))


def save_handoff(plan: HandoffPlan, destination: str | Path) -> Path:
    path = check_destination(destination, tuple(plan.evidence.manifest["blocked_root_hashes"]))
    path.mkdir(parents=True, exist_ok=False)
    plan.evidence.save(path / "evidence")
    write_json_exclusive(path / "preview.json", plan.preview())
    write_json_exclusive(path / "plan.json", {"sha256": plan.sha256, "plan": plan.data})
    return path


def load_handoff(directory: str | Path, *, max_bytes: int = 100_000_000) -> HandoffPlan:
    path = Path(directory)
    files = [path / "plan.json", path / "preview.json"]
    if any(p.is_symlink() or any(a.is_symlink() for a in p.parents) for p in files):
        raise ValueError("Handoff paths must not be symlinks")
    size = sum(p.stat().st_size for p in files)
    if size >= max_bytes:
        raise ValueError("Handoff exceeds storage limit")
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    evidence = EvidenceSnapshot.load(path / "evidence", max_bytes=max_bytes - size)
    plan = HandoffPlan(evidence, canonical_json(saved["plan"]))
    if saved.get("sha256") != plan.sha256:
        raise ValueError("Handoff plan hash mismatch")
    if json.loads(files[1].read_text(encoding="utf-8")) != plan.preview():
        raise ValueError("Handoff preview mismatch")
    return plan
