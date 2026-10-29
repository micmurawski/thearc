"""A project's sessions: the harness stores that hold them, and the cwd / tool-call filters that pick them."""

import json

from thearc.sessions import Sessions
from thearc.sessions.stores import store_folders


def line(native, cwd, content, kind="user"):
    return {"type": kind, "sessionId": native, "cwd": cwd, "timestamp": "2026-10-18T10:00:00Z",
            "message": {"role": kind, "content": content}}


def write(path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_a_projects_claude_sessions_are_found_and_evolve_runs_left_out(tmp_path):
    project = tmp_path / "work" / "app"
    (project / "src").mkdir(parents=True)
    home = tmp_path / "claude-home"
    store = home / "projects"
    mangled = str(project).replace("/", "-").replace("_", "-").replace(".", "-")
    write(store / mangled / "a.jsonl", line("a", str(project), "Fix the bug"))
    write(store / f"{mangled}-src" / "b.jsonl", line("b", str(project / "src"), "Add a test"))
    write(store / f"{mangled}-src" / "c.jsonl", line("c", str(project / "src"), "Evolve"),
          line("c", str(project / "src"), [{"type": "tool_use", "id": "t", "name": "Bash",
                                            "input": {"command": "thearc evolve --json"}}], kind="assistant"))
    write(store / f"{mangled}x" / "d.jsonl", line("d", f"{project}x", "Elsewhere"))  # A sibling, not below.
    write(store / "-other" / "e.jsonl", line("e", "/other", "Unrelated"))

    folders = store_folders("claude", project, home=home)
    assert [f.name for f in folders] == [mangled, f"{mangled}-src"]
    with Sessions(tmp_path / "index.sqlite") as sessions:
        for folder in folders:
            sessions.add(folder, harness="claude")
        sessions.add(store / f"{mangled}x", harness="claude")
        found = {s.native_id for s in sessions.list(cwd=project)}
        assert found == {"a", "b", "c"}
        assert {s.native_id for s in sessions.list(cwd=project, without_call="thearc evolve")} == {"a", "b"}
        assert {s.native_id for s in sessions.list(cwd=project / "src")} == {"b", "c"}


def test_pi_folders_and_codex_store(tmp_path):
    project = tmp_path / "app"
    pi = tmp_path / "pi" / "sessions"
    name = "--" + str(project).strip("/").replace("/", "-") + "--"
    (pi / name).mkdir(parents=True)
    (pi / (name.removesuffix("--") + "-lib--")).mkdir()
    (pi / "--elsewhere--").mkdir()
    assert [f.name for f in store_folders("pi", project, home=tmp_path / "pi")] == sorted(
        [name, name.removesuffix("--") + "-lib--"])
    (tmp_path / "codex" / "sessions").mkdir(parents=True)
    assert store_folders("codex", project, home=tmp_path / "codex") == [tmp_path / "codex" / "sessions"]
