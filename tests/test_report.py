"""The evolution dataset and its HTML report, from a workspace with a reflection and a curation."""

import json
import re

from click.testing import CliRunner

from thearc.cli import main
from thearc.curation import Curator
from thearc.resources.evolution import evolution
from thearc.resources.report import render_report

from .test_curation import ScriptedCurator, edit


def curated(workspace, reflection):
    def script(root, tool):
        edit(root, "## Limits", "## Retry budget")  # Renamed, marker kept: a move.
        tool("commit_change", message="Name the limits a budget")
        edit(root, "max 3", "max 1")
        tool("commit_change", message="One retry is enough")
        tool("finish", summary="Done.")

    return Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [reflection], workspace)


def test_the_dataset_follows_sections_across_branches_and_renames(reviewed, tmp_path):
    workspace, reflection = reviewed
    curation = curated(workspace, reflection)
    data = evolution(workspace.workspace)
    assert {b["name"] for b in data["branches"]} == {"main", curation.branch}
    assert data["scales"] == [{"name": "usefulness", "labels": ["harmful"], "ordered": True, "numeric": False,
                               "meanings": {}}]
    limits = next(s for s in data["sections"] if s["section"] == "Retry > Retry budget")
    assert limits["names"][0]["previous"] == "skill:retry#Retry > Limits"  # Renamed, same lineage.
    (rating,) = [r for r in data["ratings"] if r["section"] == limits["id"]]
    assert rating["value"] == "harmful" and rating["reflection"] == reflection.id
    kinds = [c["kind"] for c in data["changes"] if c["section"] == limits["id"]]
    assert kinds[-2:] == ["moved", "modified"] or set(kinds) >= {"moved", "modified"}
    modified = next(c for c in data["changes"] if c["section"] == limits["id"] and c["kind"] == "modified")
    assert modified["reason"] == "One retry is enough" and "+max 1" in modified["diff"]
    assert curation.branch in modified["branches"]
    first, *later = data["revisions"]
    assert data["cells"][limits["id"]][first["revision"]]["ratings"] == {"usefulness": {"harmful": 1}}
    assert later and later[-1]["spans"][limits["id"]][0] == "skills/retry/SKILL.md"
    assert "max 1" in data["files"][later[-1]["files"]["skills/retry/SKILL.md"]]  # Texts are stored once, by hash.


def test_the_report_embeds_the_dataset_safely(reviewed, tmp_path):
    workspace, _ = reviewed
    data = evolution(workspace.workspace)
    data["changes"][0]["reason"] = "</script><script>alert(1)</script>"
    page = render_report(data)
    assert page.startswith("<!doctype html>") and "<title>demo evolution</title>" in page
    embedded = re.search(r'<script type="application/json" id="thearc-data">(.*?)</script>', page, re.DOTALL)[1]
    assert json.loads(embedded) == json.loads(json.dumps(data))  # Escaping keeps the JSON intact…
    assert "</script><script>alert" not in page  # …and keeps it inside its script element.
    assert not render_report(data, standalone=False).startswith("<!doctype")


def test_the_report_command(reviewed, tmp_path):
    workspace, reflection = reviewed
    folder = tmp_path / "reflections"
    folder.mkdir()
    reflection.dump_items_to_json(folder / "one.json")
    result = CliRunner().invoke(main, ["report", str(workspace.workspace), "-o", str(tmp_path / "r.html"),
                                       "--reflections", str(folder)])
    assert result.exit_code == 0, result.output
    page = (tmp_path / "r.html").read_text()
    data = json.loads(re.search(r'id="thearc-data">(.*?)</script>', page, re.DOTALL)[1])
    assert reflection.id in data["reflections"] and "## Ratings" not in data["reflections"][reflection.id]["markdown"]


def test_hidden_views_stay_hidden(reviewed):
    # Views set their own display (the ratings view is a grid); the hidden attribute must still win.
    page = render_report(evolution(reviewed[0].workspace))
    assert "[hidden] { display: none !important; }" in page
    assert '<section id="view-sections" class="bench" hidden>' in page
