"""The evolution report: one self-contained HTML file showing how a workspace's agent evolved.

    write_report("artifacts/graphify", "report.html", reflections="artifacts/reflections")

The page embeds the evolution dataset (``thearc.resources.evolution``) and renders four views:
ratings per section and revision, each section's history (changes with reasons and diffs, ratings
with their reflections), branches and commits, and the configuration files at any revision.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from thearc.resources.evolution import evolution
from thearc.resources.ratings import RatingScale


def render_report(dataset: dict[str, Any], *, standalone: bool = True) -> str:
    """The report page for ``dataset``; ``standalone=False`` omits the document skeleton (for hosts that add it)."""
    template = files("thearc.resources").joinpath("report.html").read_text(encoding="utf-8")
    payload = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    agent = dataset.get("agent", {})
    title = html.escape(f"{agent.get('name', 'Agent')} evolution")
    page = template.replace("__THEARC_TITLE__", title).replace("__THEARC_DATA__", payload)
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "</head>\n<body>\n" + page + "\n</body>\n</html>\n")


def write_report(
    workspace: str | Path,
    path: str | Path,
    *,
    refs: Sequence[str] | None = None,
    scales: Sequence[RatingScale] = (),
    reflections: str | Path | None = None,
    standalone: bool = True,
) -> Path:
    """Build the evolution dataset of ``workspace`` and write its report to ``path``."""
    dataset = evolution(workspace, refs=refs, scales=scales, reflections=reflections)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(dataset, standalone=standalone), encoding="utf-8")
    return target
