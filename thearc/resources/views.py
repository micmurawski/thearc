"""Views: the agent rendered for people and curators, optionally with lineage IDs and rating summaries.

Views are read-only. They are never what a reflector reads, never hashed into a
revision, and never parsed back. ``agent_outline`` is where ratings are shown: in a
curator's prompt, and as ``RATINGS.md`` in a release.
"""

from __future__ import annotations

from collections.abc import Sequence

from thearc.resources.agent import MetaAgent
from thearc.resources.ratings import RatingPolicy, RatingScale, Summary, count_all
from thearc.resources.targets import ResourceTarget

Scales = RatingScale | Sequence[RatingScale]


def _scales(scales: Scales) -> tuple[RatingScale, ...]:
    scales = (scales,) if isinstance(scales, RatingScale) else tuple(scales)
    if len({scale.name for scale in scales}) != len(scales):
        raise ValueError("scale names must be distinct")
    return scales


def _labels(
    agent: MetaAgent, scales: tuple[RatingScale, ...], policy: RatingPolicy, ids: bool
) -> dict[ResourceTarget, str]:
    """Suffix per target: ``[res_…]`` (lineage ID) when ``ids``, then each scale's summary (named when there are
    several)."""
    from thearc.resources.workspace import lineage_ids

    lineage = lineage_ids(agent) if ids else {}
    summaries: list[tuple[RatingScale, dict[ResourceTarget, Summary]]] = [
        (scale, agent.journal.summaries(scale, policy)) for scale in scales
    ]
    labels: dict[ResourceTarget, str] = {}
    for target in agent.files().targets:
        parts = [f"[{lineage[target]}]"] if ids else []
        parts += [
            scale.render(by_target[target]) if len(scales) == 1 else f"{scale.name}: {scale.render(by_target[target])}"
            for scale, by_target in summaries
            if target in by_target
        ]
        if parts:
            labels[target] = " ".join(parts)
    return labels


def agent_outline(agent: MetaAgent, scales: Scales = (), *, ids: bool = False, policy: RatingPolicy = count_all) -> str:
    """Markdown outline of every resource and section with its file reference, and optionally IDs and summaries."""
    files = agent.files()
    labels = _labels(agent, _scales(scales), policy, ids)
    lines = [
        f"# {agent.name}" + (f" {agent.version}" if agent.version else ""),
        "",
        f"Revision `{agent.revision}`.",
        "",
    ]
    for target in files.targets:
        span = files.span(target)
        depth = span.depth if target.section is not None else 0
        title = target.section.rsplit(" > ", 1)[-1] if target.section else f"{target.kind}: {target.name}"
        label = f" {labels[target]}" if target in labels else ""
        lines.append(f"{'  ' * depth}- {title} `{span.reference}`{label}")
    return "\n".join(lines) + "\n"
