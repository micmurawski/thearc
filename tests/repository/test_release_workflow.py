"""Release publishing is isolated from builds and only happens for explicit releases."""


import yaml

from tests.paths import REPOSITORY_ROOT


def test_release_workflow_gates_and_permissions():
    root = REPOSITORY_ROOT
    workflow = yaml.load((root / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["release"]["types"] == ["published"]
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["test"]["strategy"]["matrix"]["python"] == ["3.11", "3.13"]
    assert jobs["build"]["needs"] == "test"
    publish = jobs["publish"]
    assert publish["needs"] == "build"
    assert publish["if"] == "github.event_name == 'release' && github.repository == 'micmurawski/thearc'"
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["environment"]["name"] == "pypi"
    assert all("run" not in step and "checkout" not in step["uses"] for step in publish["steps"])
    assert publish["steps"][-1]["uses"] == "pypa/gh-action-pypi-publish@release/v1"
    assert "with" not in publish["steps"][-1]  # No password, skip-existing, or alternate index.
    guard = next(step for step in jobs["build"]["steps"] if step.get("name") == "Validate release tag")
    assert guard["if"] == "github.event_name == 'release'"
    assert guard["env"]["RELEASE_TAG"] == "${{ github.event.release.tag_name }}"
    assert "${{" not in guard["run"]  # Untrusted tag values never become shell source.
