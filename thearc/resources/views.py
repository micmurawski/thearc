"""Views: the agent rendered for people and curators, optionally with target IDs and rating summaries.

Views are read-only. They are never what a reflector reads, never hashed into a
revision, and never parsed back.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from thearc.resources.agent import MetaAgent
from thearc.resources.markers import code_comment as _code_comment
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
    """Suffix per target: ``[res_…]`` when ``ids``, then each scale's summary (named when there are several)."""
    summaries: list[tuple[RatingScale, dict[ResourceTarget, Summary]]] = [
        (scale, agent.journal.summaries(scale, policy)) for scale in scales
    ]
    labels: dict[ResourceTarget, str] = {}
    for target in agent.files().targets:
        parts = [f"[{target.id}]"] if ids else []
        parts += [
            scale.render(by_target[target]) if len(scales) == 1 else f"{scale.name}: {scale.render(by_target[target])}"
            for scale, by_target in summaries
            if target in by_target
        ]
        if parts:
            labels[target] = " ".join(parts)
    return labels


def annotated_files(
    agent: MetaAgent, scales: Scales = (), *, ids: bool = False, policy: RatingPolicy = count_all
) -> dict[str, str]:
    """Agent files with IDs and/or rating summaries added to the lines that name a target.

    A Markdown section's label follows its heading. A code file (``.sh``, ``.py``, ``.ts``, …) has one label for
    the whole file, as a comment at the end of its first line (after a shebang, the second). Only those lines
    change, so line numbers and file references stay valid. Other whole resources appear in ``agent_outline``.
    """
    files = agent.files()
    additions: dict[str, dict[int, str]] = defaultdict(dict)  # path -> line index -> text to append
    for target, label in _labels(agent, _scales(scales), policy, ids).items():
        span = files.span(target)
        if target.section is not None:
            additions[span.path][span.start - 1] = f" {label}"
        elif span.depth == 0 and (comment := _code_comment(span.path)) and span.end >= 1:
            shebang = files.files[span.path].startswith("#!")
            if not shebang or span.end >= 2:
                additions[span.path][1 if shebang else 0] = f"  {comment} rating: {label}"
    result = dict(files.files)
    for path, suffixes in additions.items():
        lines = result[path].splitlines(keepends=True)
        for index, suffix in suffixes.items():
            text = lines[index].rstrip("\r\n")
            line = f"{text}{suffix}" if text.strip() else suffix.lstrip()
            lines[index] = line + lines[index][len(text) :]
        result[path] = "".join(lines)
    return result


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
