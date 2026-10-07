"""Resource targets: stable addresses of resources and their Markdown sections."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Kind = Literal["skill", "skill_file", "context", "hook", "mcp", "rule", "workflow", "command"]
FILE_KINDS = frozenset({"skill_file", "context", "rule", "workflow", "command"})
STRUCTURED_KINDS = frozenset({"hook", "mcp"})


def safe_relative_path(value: str) -> str:
    """Validate a relative POSIX path that cannot escape its directory."""
    parts = value.split("/")
    if (not value or "\\" in value or "\x00" in value or ":" in value or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(f"unsafe relative path: {value!r}")
    return value


def safe_name(value: str) -> str:
    """Validate a single, non-blank, single-line path component."""
    if (not value or value != value.strip() or value in {".", ".."} or any(char in value for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"unsafe name: {value!r}")
    return value


class ResourceTarget(BaseModel):
    """Address of a resource, optionally narrowed to one Markdown section.

    ``name`` is a skill, hook, or MCP name, a context filename, a rule/workflow/command
    path, or ``<skill>/<relative-path>`` for a skill file. ``section`` is the heading
    path joined by ``" > "``, e.g. ``"Usage > Examples"``.
    """

    model_config = {"frozen": True, "extra": "forbid"}
    kind: Kind
    name: str = Field(min_length=1)
    section: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_address(self) -> ResourceTarget:
        if self.section is not None and not self.section.strip():
            raise ValueError("section cannot be blank")
        if self.kind in STRUCTURED_KINDS and self.section is not None:
            raise ValueError("hooks and MCPs have no Markdown sections")
        if self.kind == "skill_file":
            skill, separator, path = self.name.partition("/")
            if not separator:
                raise ValueError("skill file target must be '<skill>/<relative-path>'")
            safe_name(skill)
            safe_relative_path(path)
        elif self.kind in FILE_KINDS:
            safe_relative_path(self.name)
        else:
            safe_name(self.name)
        return self

    @property
    def id(self) -> str:
        """Derived from the address only; content and ratings never change it."""
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "res_" + hashlib.sha256(payload.encode()).hexdigest()[:24]

    @property
    def resource(self) -> ResourceTarget:
        """The whole resource this target belongs to."""
        return self if self.section is None else self.model_copy(update={"section": None})

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}" + (f"#{self.section}" if self.section else "")
