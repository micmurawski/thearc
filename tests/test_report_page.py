"""The report page's script, run in Node against a minimal DOM: every view renders, selection and toggles work."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from thearc.curation import Merger
from thearc.resources import CategoricalScale, MetaAgent
from thearc.resources.evolution import evolution
from thearc.resources.report import render_report, write_report

from .test_curation import ScriptedCurator, edit

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("report_page.js")


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page's script")
def test_the_page_renders_every_view(candidates, tmp_path):
    def merger(root, tool):
        edit(root, "max 3", "max 2")
        assert not tool("commit_change", message="Combine the limits")["error"]
        tool("finish", summary="Combined.")

    idle = MetaAgent.from_workspace(candidates.workspace.workspace).to_worktree(tmp_path / "idle")  # Changed nothing.
    Merger(runtime=ScriptedCurator(merger)).merge("Merge.", [candidates.one, candidates.two, idle]).agent.release()
    page = write_report(candidates.workspace.workspace, tmp_path / "report.html")
    result = json.loads(subprocess.run([NODE, str(HARNESS), str(page)], check=True, capture_output=True,
                                       text=True, timeout=60).stdout)

    assert result["graphRows"] >= 4  # Two candidates' commits, the merger's, the release, the base.
    assert result["mergedFromEdges"] == 3 and result["idleRows"] == 1  # The idle candidate still shows.
    assert result["gridCells"] > 0 and result["changeCommits"] > 0 and result["sectionVersions"] >= 3
    assert result["selected"] and result["detail"] > 0
    assert result["documentLines"] > 0  # File texts resolve from the dataset's shared table.
    assert result["theme"] == "light"  # Dark by default here; one click switches.
    assert result["graphRowsWithBookkeeping"] > result["graphRows"]
    charts = result["charts"]  # The opened section's chart, in each view; the last one chosen is remembered.
    assert all(view["pressed"] for view in charts.values()) and result["chartStored"] == "score"
    assert charts["rounds"]["bars"] > 0 and charts["versions"]["bars"] > 0 and charts["score"]["dots"] > 0


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page's script")
def test_the_charts_draw_any_scale_in_its_own_colours(candidates, tmp_path):
    importance = CategoricalScale(
        "importance", ["meaningless", "significant", "important", "very important", "critical"],
        values={"meaningless": 0, "significant": 1, "important": 2, "very important": 3, "critical": 4},
        colors={"critical": "#c0392b"})
    renamed = {"harmful": "meaningless", "neutral": "important", "helpful": "critical"}
    dataset = evolution(candidates.workspace.workspace, scales=[importance])
    for rating in dataset["ratings"]:  # The fixture's usefulness ratings, read as importance.
        rating["scale"], rating["value"] = "importance", renamed[rating["value"]]
    for revisions in dataset["cells"].values():
        for cell in revisions.values():
            counts = cell["ratings"].pop("usefulness", {})
            cell["ratings"]["importance"] = {renamed[value]: n for value, n in counts.items()}
    dataset["scales"] = [scale for scale in dataset["scales"] if scale["name"] == "importance"]
    assert dataset["scales"][0]["values"]["critical"] == 4 and dataset["scales"][0]["colors"] == {"critical": "#c0392b"}
    page = tmp_path / "report.html"
    page.write_text(render_report(dataset), encoding="utf-8")
    result = json.loads(subprocess.run([NODE, str(HARNESS), str(page)], check=True, capture_output=True,
                                       text=True, timeout=60).stdout)

    chart = result["scoreChart"]
    assert result["charts"]["score"]["dots"] > 0 and "#c0392b" in chart  # Its own colour, in the legend and dots.
    assert ">0.5</span>" in chart and ">-1</span>" not in chart  # A sequential scale's score runs from 0 to 1.
