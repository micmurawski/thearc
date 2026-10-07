"""Views: the agent rendered for people and curators, optionally with target IDs and rating summaries.

Views are read-only. They are never what a reflector reads, never hashed into a
revision, and never parsed back.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from thearcv2.resources.agent import MetaAgent
from thearcv2.resources.ratings import RatingPolicy, RatingScale, Summary, count_all
from thearcv2.resources.targets import ResourceTarget

Scales = RatingScale | Sequence[RatingScale]


def _scales(scales: Scales) -> tuple[RatingScale, ...]:
    scales = (scales,) if isinstance(scales, RatingScale) else tuple(scales)
    if len({scale.name for scale in scales}) != len(scales):
        raise ValueError("scale names must be distinct")
    return scales


def _labels(agent: MetaAgent, scales: tuple[RatingScale, ...], policy: RatingPolicy,
            ids: bool) -> dict[ResourceTarget, str]:
    """Suffix per target: ``[res_…]`` when ``ids``, then each scale's summary (named when there are several)."""
    summaries: list[tuple[RatingScale, dict[ResourceTarget, Summary]]] = [
        (scale, agent.journal.summaries(scale, policy)) for scale in scales]
    labels: dict[ResourceTarget, str] = {}
    for target in agent.files().targets:
        parts = [f"[{target.id}]"] if ids else []
        parts += [scale.render(by_target[target]) if len(scales) == 1 else f"{scale.name}: "
                  f"{scale.render(by_target[target])}" for scale, by_target in summaries if target in by_target]
        if parts:
            labels[target] = " ".join(parts)
    return labels


def annotated_files(agent: MetaAgent, scales: Scales = (), *, ids: bool = False,
                    policy: RatingPolicy = count_all) -> dict[str, str]:
    """Agent files with each section heading suffixed by its ID and/or rating summaries.

    Only heading lines change, so line numbers and file references stay valid.
    Whole resources have no heading to carry a suffix; see ``agent_outline``.
    """
    files = agent.files()
    headings: dict[str, dict[int, str]] = defaultdict(dict)
    for target, label in _labels(agent, _scales(scales), policy, ids).items():
        if target.section is not None:
            span = files.span(target)
            headings[span.path][span.start] = label
    result = dict(files.files)
    for path, suffixes in headings.items():
        lines = result[path].splitlines(keepends=True)
        for number, label in suffixes.items():
            text = lines[number - 1].rstrip("\r\n")
            lines[number - 1] = f"{text} {label}{lines[number - 1][len(text):]}"
        result[path] = "".join(lines)
    return result


def agent_outline(agent: MetaAgent, scales: Scales = (), *, ids: bool = False,
                  policy: RatingPolicy = count_all) -> str:
    """Markdown outline of every resource and section with its file reference, and optionally IDs and summaries."""
    files = agent.files()
    labels = _labels(agent, _scales(scales), policy, ids)
    lines = [f"# {agent.name}" + (f" {agent.version}" if agent.version else ""), "",
             f"Revision `{files.revision}`.", ""]
    for target in files.targets:
        span = files.span(target)
        depth = span.depth if target.section is not None else 0
        title = target.section.rsplit(" > ", 1)[-1] if target.section else f"{target.kind}: {target.name}"
        label = f" {labels[target]}" if target in labels else ""
        lines.append(f"{'  ' * depth}- {title} `{span.reference}`{label}")
    return "\n".join(lines) + "\n"
