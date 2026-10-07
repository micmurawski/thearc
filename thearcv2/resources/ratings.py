"""Rating scales, mergeable rating summaries, and rating policies.

``thearc`` fixes no scale. A scale validates rating values, folds them into a
summary, and renders that summary; summaries from disjoint sets of ratings merge
into the summary of their union.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from thearcv2.resources.journal import Entry, Rated

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
        return NumericSummary(self.count + other.count, self.total + other.total,
                              min(self.minimum, other.minimum), max(self.maximum, other.maximum))


Summary = CountSummary | NumericSummary


class RatingScale(ABC):
    """Defines the values a rating may take and how ratings combine into a summary."""

    def __init__(self, name: str):
        if not name or name != name.strip():
            raise ValueError("scale name must be a non-blank, trimmed string")
        self.name = name

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
    """A fixed set of labels, optionally ordered (e.g. ``harmful < neutral < helpful``)."""

    def __init__(self, name: str, labels: Sequence[str], *, ordered: bool = False):
        super().__init__(name)
        if not labels or len(set(labels)) != len(labels) or any(not isinstance(label, str) or not label
                                                                 for label in labels):
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
        return summary.merge(CountSummary({value: weight}))

    def render(self, summary: Summary) -> str:
        assert isinstance(summary, CountSummary)
        return "(" + ", ".join(f"{label}: {_number(summary.counts.get(label, 0.0))}" for label in self.labels) + ")"

    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "enum": list(self.labels)}


def normalize_label(value: str) -> str:
    """Trim, lowercase, and collapse internal whitespace."""
    return " ".join(value.split()).lower()


class LabelScale(RatingScale):
    """Open-ended labels, counted like tags; ``normalize`` keeps one label from counting under two spellings."""

    def __init__(self, name: str, *, normalize: Callable[[str], str] = normalize_label, max_length: int = 100):
        super().__init__(name)
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
        return summary.merge(CountSummary({value: weight}))

    def render(self, summary: Summary) -> str:
        assert isinstance(summary, CountSummary)
        ordered = sorted(summary.counts.items(), key=lambda item: (-item[1], item[0]))
        return "(" + ", ".join(f"{label}: {_number(count)}" for label, count in ordered) + ")"

    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "minLength": 1, "maxLength": self.max_length}


class NumericScale(RatingScale):
    """Finite numbers, optionally bounded (inclusive)."""

    def __init__(self, name: str, *, minimum: float | None = None, maximum: float | None = None):
        super().__init__(name)
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
        return schema


# A rating policy reads one target's history (oldest first, across moves) and
# decides which ratings count, and with what weight. Pipelines choose the policy.
RatingPolicy = Callable[[Sequence["Entry"]], Iterable[tuple["Rated", float]]]


def count_all(history: Sequence[Entry]) -> Iterable[tuple[Rated, float]]:
    """Every rating counts once."""
    return ((entry, 1.0) for entry in history if entry.kind == "rated")


def since_last_modification(history: Sequence[Entry]) -> Iterable[tuple[Rated, float]]:
    """Only ratings given after the target's content last changed."""
    start = max((index for index, entry in enumerate(history) if entry.kind in {"added", "modified"}), default=-1)
    return ((entry, 1.0) for entry in history[start + 1:] if entry.kind == "rated")
