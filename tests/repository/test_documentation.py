"""Optional docs-extra smoke test: build public documentation without inference."""

import ast
import json
import re
import subprocess
import sys

import pytest
import yaml

from tests.paths import REPOSITORY_ROOT

pytest.importorskip("mkdocs", reason="Install the docs extra to validate the documentation site")


def test_documentation_builds_strictly_without_private_artifacts(tmp_path):
    root = REPOSITORY_ROOT
    site = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=root, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (site / "index.html").is_file()
    assert "Your first ACE run" in (site / "start/first-ace/index.html").read_text()
    assert (site / "search/search_index.json").is_file()
    assert (site / "guides/sessions/index.html").is_file()
    assert (site / "reference/runtimes/index.html").is_file()
    for excluded in ("logs", "tests", ".codex", ".agents", "_hooks.py", "archive", "cookbook", "prompts"):
        assert not (site / excluded).exists()
    search = json.loads((site / "search/search_index.json").read_text())
    assert all("archive/" not in item["location"] for item in search["docs"])
    assert not list(site.rglob("*.py"))
    assert not list(site.rglob("*.pyc"))


def test_documentation_python_examples_parse():
    docs = REPOSITORY_ROOT / "docs"
    for path in docs.rglob("*.md"):
        for snippet in re.findall(r"```python\n(.*?)```", path.read_text(), re.DOTALL):
            ast.parse(snippet, filename=str(path))


def test_repository_documentation_links_resolve():
    root = REPOSITORY_ROOT
    for source in (root / "README.md", root / "GOALS.md", *sorted((root / "cookbook").glob("*.md"))):
        for link in re.findall(r"\]\(([^)]+)\)", source.read_text()):
            if "docs/" in link and "://" not in link:
                target = link.split("#", 1)[0]
                assert (source.parent / target).is_file(), f"{source}: broken documentation link {link}"


def test_pages_workflow_only_deploys_built_site_from_main():
    root = REPOSITORY_ROOT
    workflow = yaml.safe_load((root / ".github/workflows/docs.yml").read_text())
    build = workflow["jobs"]["build"]
    deploy = workflow["jobs"]["deploy"]
    upload = next(step for step in build["steps"] if "upload-pages-artifact" in step.get("uses", ""))
    assert upload["with"]["path"] == "site"
    assert upload["if"] == deploy["if"] == "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'"
    assert deploy["needs"] == "build"
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert workflow["permissions"] == {"contents": "read"}
    assert any(step.get("run") == "python -m mkdocs build --strict" for step in build["steps"])
    assert not any("jekyll" in step.get("uses", "") for step in build["steps"])
    assert yaml.safe_load((root / "mkdocs.yml").read_text())["site_url"] == "https://micmurawski.github.io/thearc/"
