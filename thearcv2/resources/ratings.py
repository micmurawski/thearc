"""Rating scales, mergeable rating summaries, and rating policies.

``thearc`` fixes no scale. A scale validates rating values, folds them into a
summary, and renders that summary; summaries from disjoint sets of ratings merge
into the summary of their union.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from thearcv2.resources.journal import Journal, Lineage, Rated

RatingValue = str | float


def _number(value: float) -> str:
    return f"{value:g}"


@dataclass(frozen=True)
class CountSummary:
    """Weighted count per label."""

    counts: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))

    @property
    def total(self) -> float:
        return sum(self.counts.values())

    def merge(self, other: CountSummary) -> CountSummary:
        merged = dict(self.counts)
        for label, count in other.counts.items():
            merged[label] = merged.get(label, 0.0) + count
        return CountSummary(merged)


@dataclass(frozen=True)
class NumericSummary:
    """Weighted count and sum, plus the extremes; the mean is derived so summaries stay mergeable."""

    count: float = 0.0
    total: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf

    @property
    def mean(self) -> float | None:
        return self.total / self.count if self.count else None

    def merge(self, other: NumericSummary) -> NumericSummary:
        return NumericSummary(
            self.count + other.count,
            self.total + other.total,
            min(self.minimum, other.minimum),
            max(self.maximum, other.maximum),
        )


Summary = CountSummary | NumericSummary


class RatingScale(ABC):
    """Defines the values a rating may take and how ratings combine into a summary.

    ``description`` says what the scale measures; it reaches reflectors through the JSON Schema.
    """

    def __init__(self, name: str, *, description: str = ""):
        if not name or name != name.strip():
            raise ValueError("scale name must be a non-blank, trimmed string")
        self.name = name
        self.description = description

    @abstractmethod
    def validate(self, value: Any) -> RatingValue:
        """Return the canonical value, or raise ``ValueError``."""

    @abstractmethod
    def empty(self) -> Summary:
        """The summary of no ratings."""

    @abstractmethod
    def add(self, summary: Summary, value: RatingValue, weight: float = 1.0) -> Summary:
        """Fold one validated value into ``summary``."""

    @abstractmethod
    def render(self, summary: Summary) -> str:
        """Short text shown next to a target, e.g. ``(harmful: 1, neutral: 0, helpful: 3)``."""

    def field(self) -> Any:
        """``Annotated[Any, scale]``: a rating on this scale in a reflection schema, under any field name.

        Type checkers prefer the spelled-out form, which works the same way::

            class Rated(BaseModel):
                ref: Ref
                usefulness: Annotated[str, USEFULNESS]
        """
        return Annotated[Any, self]

    def __get_pydantic_core_schema__(self, source: Any, handler: Any) -> Any:
        """As Pydantic metadata, a scale validates its field's value."""
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(self.validate, handler(source))

    def __get_pydantic_json_schema__(self, schema: Any, handler: Any) -> dict[str, Any]:
        """…and describes it: the scale's own JSON Schema, headed by its name."""
        result = self.json_schema()
        heading = f"Rating on the {self.name!r} scale."
        return {**result, "description": f"{heading}\n{result['description']}" if "description" in result
                else heading}

    @abstractmethod
    def json_schema(self) -> dict[str, Any]:
        """JSON Schema of a single rating value."""

    def summarize(self, weighted: Iterable[tuple[RatingValue, float]]) -> Summary | None:
        """Summary of ``(value, weight)`` pairs; ``None`` when there are none."""
        summary, seen = self.empty(), False
        for value, weight in weighted:
            summary, seen = self.add(summary, self.validate(value), weight), True
        return summary if seen else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


class CategoricalScale(RatingScale):
    """A fixed set of labels, optionally ordered (e.g. ``harmful < neutral < helpful``).

    ``labels`` may map each label to what it means; reflectors see those meanings.
    """

    def __init__(
        self, name: str, labels: Sequence[str] | Mapping[str, str], *, ordered: bool = False, description: str = ""
    ):
        super().__init__(name, description=description)
        self.meanings = dict(labels) if isinstance(labels, Mapping) else {}
        labels = list(labels)
        if (
            not labels
            or len(set(labels)) != len(labels)
            or any(not isinstance(label, str) or not label for label in labels)
        ):
            raise ValueError("labels must be distinct, non-empty strings")
        self.labels = tuple(labels)
        self.ordered = ordered

    def validate(self, value: Any) -> str:
        if value not in self.labels or not isinstance(value, str):
            raise ValueError(f"{value!r} is not one of {list(self.labels)}")
        return value

    def empty(self) -> CountSummary:
        return CountSummary({label: 0.0 for label in self.labels})

    def add(self, summary: Summary, value: RatingValue, weight: float = 1.0) -> CountSummary:
        assert isinstance(summary, CountSummary)
        return summary.merge(CountSummary({str(value): weight}))

    def render(self, summary: Summary) -> str:
        assert isinstance(summary, CountSummary)
        return "(" + ", ".join(f"{label}: {_number(summary.counts.get(label, 0.0))}" for label in self.labels) + ")"

    def json_schema(self) -> dict[str, Any]:
        lines = [self.description] if self.description else []
        if self.ordered:
            lines.append("Ordered: " + " < ".join(self.labels) + ".")
        lines += [f"- {label}: {meaning}" for label, meaning in self.meanings.items() if meaning]
        schema: dict[str, Any] = {"type": "string", "enum": list(self.labels)}
        return {**schema, "description": "\n".join(lines)} if lines else schema


def normalize_label(value: str) -> str:
    """Trim, lowercase, and collapse internal whitespace."""
    return " ".join(value.split()).lower()


class LabelScale(RatingScale):
    """Open-ended labels, counted like tags; ``normalize`` keeps one label from counting under two spellings."""

    def __init__(
        self,
        name: str,
        *,
        normalize: Callable[[str], str] = normalize_label,
        max_length: int = 100,
        description: str = "",
    ):
        super().__init__(name, description=description)
        self.normalize = normalize
        self.max_length = max_length

    def validate(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"label must be a string, got {type(value).__name__}")  # noqa: TRY004 -- a rejection
        label = self.normalize(value)
        if not label or len(label) > self.max_length or any(ord(char) < 32 for char in label):
            raise ValueError(f"invalid label: {value!r}")
        return label

    def empty(self) -> CountSummary:
        return CountSummary()

    def add(self, summary: Summary, value: RatingValue, weight: float = 1.0) -> CountSummary:
        assert isinstance(summary, CountSummary)
        return summary.merge(CountSummary({str(value): weight}))

    def render(self, summary: Summary) -> str:
        assert isinstance(summary, CountSummary)
        ordered = sorted(summary.counts.items(), key=lambda item: (-item[1], item[0]))
        return "(" + ", ".join(f"{label}: {_number(count)}" for label, count in ordered) + ")"

    def json_schema(self) -> dict[str, Any]:
        schema = {"type": "string", "minLength": 1, "maxLength": self.max_length}
        return {**schema, "description": self.description} if self.description else schema


class NumericScale(RatingScale):
    """Finite numbers, optionally bounded (inclusive)."""

    def __init__(
        self, name: str, *, minimum: float | None = None, maximum: float | None = None, description: str = ""
    ):
        super().__init__(name, description=description)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum must not exceed maximum")
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"rating must be a finite number, got {value!r}")
        if (self.minimum is not None and value < self.minimum) or (self.maximum is not None and value > self.maximum):
            raise ValueError(f"{value} is outside [{self.minimum}, {self.maximum}]")
        return float(value)

    def empty(self) -> NumericSummary:
        return NumericSummary()

    def add(self, summary: Summary, value: RatingValue, weight: float = 1.0) -> NumericSummary:
        assert isinstance(summary, NumericSummary) and isinstance(value, float)
        return summary.merge(NumericSummary(weight, weight * value, value, value))

    def render(self, summary: Summary) -> str:
        assert isinstance(summary, NumericSummary)
        mean = summary.mean
        return f"(mean: {'-' if mean is None else f'{mean:.3g}'}, n: {_number(summary.count)})"

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "number"}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.description:
            schema["description"] = self.description
        return schema


# A rating policy decides which ratings count for one target, and with what weight. It gets the
# target's lineage (its history across moves, oldest first) and the journal, so it may look at other
# targets too. Pipelines choose the policy.
RatingPolicy = Callable[["Lineage", "Journal"], Iterable[tuple["Rated", float]]]


def count_all(lineage: Lineage, journal: Journal) -> Iterable[tuple[Rated, float]]:
    """Every rating of the target counts once."""
    return ((entry, 1.0) for entry in lineage.entries if entry.kind == "rated")


def since_last_modification(lineage: Lineage, journal: Journal) -> Iterable[tuple[Rated, float]]:
    """Only ratings given after the target's content last changed."""
    history = lineage.entries
    start = max((index for index, entry in enumerate(history) if entry.kind in {"added", "modified"}), default=-1)
    return ((entry, 1.0) for entry in history[start + 1 :] if entry.kind == "rated")


def including_subsections(policy: RatingPolicy = count_all) -> RatingPolicy:
    """``policy`` applied to a section and every section under it, so ratings roll up the hierarchy.

    One judgment that covered several of those sections (one reflection item) counts once.
    """

    def rolled_up(lineage: Lineage, journal: Journal) -> Iterator[tuple[Rated, float]]:
        counted: set[str] = set()
        for other in journal.lineages():
            if not other.removed and lineage.target.contains(other.target):
                for rated, weight in policy(other, journal):
                    if rated.item_id not in counted:
                        counted.add(rated.item_id)
                        yield rated, weight

    return rolled_up
