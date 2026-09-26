"""Shared feedback counts for skills, hooks, and Markdown sections."""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class Ranks(BaseModel):
    """Non-negative feedback counts; absent ranks are distinct from zero counts."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    harmful: int = Field(default=0, ge=0, strict=True)
    neutral: int = Field(default=0, ge=0, strict=True)
    helpful: int = Field(default=0, ge=0, strict=True)

    def suffix(self) -> str:
        return f" (harmful: {self.harmful}, neutral: {self.neutral}, helpful: {self.helpful})"


def split_ranked_title(title: str) -> tuple[str, Ranks | None]:
    """Recognize a complete rank suffix, leaving ordinary parentheses untouched."""
    match = re.fullmatch(r"(.*?)\s+\(([^()]*)\)", title)
    if not match:
        return title, None
    pairs = [re.fullmatch(r"\s*(harmful|neutral|helpful)\s*:\s*([0-9]+)\s*", part)
             for part in match[2].split(",")]
    if len(pairs) != 3 or any(pair is None for pair in pairs):
        return title, None
    values = {pair[1]: int(pair[2]) for pair in pairs if pair is not None}
    if len(values) != 3:
        return title, None
    return match[1].rstrip(), Ranks(**values)
