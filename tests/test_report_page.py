"""The report page's script, run in Node against a minimal DOM: every view renders, selection and toggles work."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from thearc.curation import Merger
from thearc.resources.report import write_report

from .test_curation import ScriptedCurator, edit

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("report_page.js")


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page's script")
def test_the_page_renders_every_view(candidates, tmp_path):
    def merger(root, tool):
        edit(root, "max 3", "max 2")
        assert not tool("commit_change", message="Combine the limits")["error"]
        tool("finish", summary="Combined.")

    Merger(runtime=ScriptedCurator(merger)).merge("Merge.", [candidates.one, candidates.two]).agent.release()
    page = write_report(candidates.workspace.workspace, tmp_path / "report.html")
    result = json.loads(subprocess.run([NODE, str(HARNESS), str(page)], check=True, capture_output=True,
                                       text=True, timeout=60).stdout)

    assert result["graphRows"] >= 4  # Two candidates' commits, the merger's, the release, the base.
    assert result["mergedFromEdges"] == 2
    assert result["gridCells"] > 0 and result["changeCommits"] > 0 and result["sectionVersions"] >= 3
    assert result["selected"] and result["detail"] > 0
    assert result["documentLines"] > 0  # File texts resolve from the dataset's shared table.
    assert result["theme"] == "light"  # Dark by default here; one click switches.
    assert result["graphRowsWithBookkeeping"] > result["graphRows"]
