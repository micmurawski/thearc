"""Reflection schema: the operator's contract for reflection output, and its conversion into Rated entries."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from thearcv2.resources.agent import MetaAgent
from thearcv2.resources.files import FileReference
from thearcv2.resources.journal import Rated
from thearcv2.resources.ratings import RatingScale, RatingValue

REFERENCE_PATTERN = r"^[^#\s]+#L[1-9][0-9]*(-L[1-9][0-9]*)?$"
_CORE = ("ref", "rating")


@dataclass(frozen=True)
class ReflectionItem:
    """One validated item: a file reference, a rating on the schema's scale, and operator metadata."""

    ref: FileReference
    rating: RatingValue
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Rejection:
    index: int
    item: Any
    reason: str


@dataclass(frozen=True)
class ReflectionResult:
    """Rated entries to record, and the items that were rejected instead."""

    entries: tuple[Rated, ...]
    rejected: tuple[Rejection, ...]


class ReflectionSchema:
    """Validates reflection output: ``ref`` and ``rating`` are fixed, everything else is operator metadata.

    ``metadata`` is a Pydantic model for the remaining fields; without one, any
    JSON-serialisable fields are accepted as they are.
    """

    def __init__(self, scale: RatingScale, metadata: type[BaseModel] | None = None):
        if metadata is not None and (clash := set(_CORE) & set(metadata.model_fields)):
            raise ValueError(f"metadata fields cannot be named {sorted(clash)}")
        self.scale = scale
        self.metadata = metadata

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema of the reflection output (a list of items), e.g. for structured output or a prompt."""
        properties: dict[str, Any] = {
            "ref": {"type": "string", "pattern": REFERENCE_PATTERN,
                    "description": "Agent file lines judged: path#Lx or path#Lx-Ly (1-based, inclusive)."},
            "rating": {**self.scale.json_schema(), "description": f"Rating on the {self.scale.name!r} scale."},
        }
        required = list(_CORE)
        item: dict[str, Any] = {"type": "object", "properties": properties, "required": required}
        if self.metadata is None:
            item["additionalProperties"] = True
        else:
            schema = self.metadata.model_json_schema()
            properties.update(schema.get("properties", {}))
            required += schema.get("required", [])
            item["additionalProperties"] = False
            if "$defs" in schema:
                return {"type": "array", "items": item, "$defs": schema["$defs"]}
        return {"type": "array", "items": item}

    def validate_item(self, raw: Any) -> ReflectionItem:
        if not isinstance(raw, dict):
            raise ValueError("item must be an object")  # noqa: TRY004 -- reported as a rejection
        missing = [key for key in _CORE if key not in raw]
        if missing:
            raise ValueError(f"missing {missing}")
        if not isinstance(raw["ref"], str):
            raise ValueError("ref must be a string")  # noqa: TRY004 -- reported as a rejection
        ref = FileReference.parse(raw["ref"])
        rating = self.scale.validate(raw["rating"])
        rest = {key: value for key, value in raw.items() if key not in _CORE}
        if self.metadata is not None:
            try:
                metadata = self.metadata.model_validate(rest).model_dump(mode="json")
            except ValidationError as error:
                raise ValueError(f"invalid metadata: {error}") from None
        else:
            try:
                metadata = json.loads(json.dumps(rest, allow_nan=False))
            except (TypeError, ValueError):
                raise ValueError("metadata must be JSON-serialisable") from None
        return ReflectionItem(ref, rating, metadata)

    def record(self, items: Iterable[Any], agent: MetaAgent, *, at: datetime | None = None,
               **extra: Any) -> ReflectionResult:
        """Validate items against ``agent`` (the revision that was judged) and build their Rated entries.

        Each item yields one entry per target its reference resolves to; those
        entries share an ``item_id``. Keyword arguments are stored on every entry as
        ``extra``. Nothing is appended to the journal here.
        """
        at = at or datetime.now(UTC)
        files = agent.files()
        revision = files.revision
        entries: list[Rated] = []
        rejected: list[Rejection] = []
        for index, raw in enumerate(items):
            try:
                item = self.validate_item(raw)
                targets = files.resolve(item.ref)
            except ValueError as error:
                rejected.append(Rejection(index, raw, str(error)))
                continue
            item_id = "item_" + uuid.uuid4().hex
            entries += [Rated(target=target, revision=revision, at=at, extra=extra, scale=self.scale.name,
                              value=item.rating, ref=str(item.ref), item_id=item_id,
                              metadata=item.metadata)
                        for target in targets]
        return ReflectionResult(tuple(entries), tuple(rejected))
