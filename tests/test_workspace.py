import json
import subprocess

import pytest

from thearc.resources import (
    Journal,
    MetaAgent,
    detect_changes,
    read_workspace,
    write_workspace,
)
from thearc.resources.git import _git
from thearc.resources.journal import Rated


def test_round_trip_with_journal(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws")
    assert read_workspace(root) == agent


def test_without_journal_for_reflection(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws", journal=False)
    assert not (root / "journal").exists()
    assert len(read_workspace(root).journal) == 0


def test_hand_edited_skill_is_read(agent, tmp_path):
    root = agent.to_workspace(tmp_path / "ws")
    path = root / "skills/retry/SKILL.md"
    path.write_text(path.read_text().replace("max 3", "max 5"))
    assert "max 5" in read_workspace(root).skills["retry"].instructions


def test_overwrite_syncs_config_and_keeps_journal(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws")
    (root / ".git").mkdir()
    smaller = agent.model_copy(deep=True)
    smaller.resources = {}
    smaller.journal.entries = smaller.journal.entries[:1]
    write_workspace(smaller, root, overwrite=True)
    assert not (root / "rules").exists() and (root / ".git").is_dir()
    assert len(read_workspace(root).journal) == len(agent.journal)


def test_journal_entries_cannot_be_rewritten(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws")
    rewritten = agent.model_copy(deep=True)
    rewritten.journal.entries[0] = rewritten.journal.entries[0].model_copy(update={"revision": "rev_x"})
    with pytest.raises(ValueError, match="differs from the recorded one"):
        write_workspace(rewritten, root, overwrite=True)


def test_journal_is_one_jsonl_file_with_union_merge(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws")
    lines = (root / "journal.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [entry.id for entry in agent.journal.entries]
    assert (root / ".gitattributes").read_text() == "journal.jsonl merge=union\n"
    agent.to_workspace(root, overwrite=True)
    assert (root / ".gitattributes").read_text() == "journal.jsonl merge=union\n"


def test_union_merged_journal_is_read(agent, human, tmp_path):
    rated = Rated(
        target=agent.files().targets[0],
        revision=agent.revision,
        extra=human,
        scale="s",
        value="x",
        ref="context/AGENTS.md#L1",
        item_id="item_1",
    )
    agent.journal.record(*detect_changes(None, agent, **human), rated)
    root = agent.to_workspace(tmp_path / "ws")
    path = root / "journal.jsonl"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join([lines[-1], *lines, lines[0]]))  # Out of order and duplicated, as git may leave it.
    assert read_workspace(root).journal == agent.journal
    path.write_text(path.read_text() + lines[0].replace('"revision":"', '"revision":"x'))
    with pytest.raises(ValueError, match="differs"):
        read_workspace(root)


def test_reflection_export_leaves_the_recorded_journal_alone(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = agent.to_workspace(tmp_path / "ws")
    before = (root / "journal.jsonl").read_text()
    write_workspace(agent.model_copy(update={"journal": type(agent.journal)()}), root, journal=False, overwrite=True)
    assert (root / "journal.jsonl").read_text() == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda root: (root / "skills" / "stray.txt").write_text("x"),  # A top-level stray belongs to the repo.
        lambda root: (root / "skills/empty").mkdir() or (root / "skills/empty/notes.md").write_text("x"),
        lambda root: (root / "hooks.json").write_text(json.dumps({"guard": {"bogus": 1}})),
        lambda root: (root / "agent.json").write_text(json.dumps({"schema_version": 1, "name": "a", "version": None})),
    ],
)
def test_invalid_workspaces_are_rejected(agent, tmp_path, mutate):
    root = agent.to_workspace(tmp_path / "ws")
    mutate(root)
    with pytest.raises(ValueError):
        read_workspace(root)


def test_refuses_existing_directory_without_overwrite(agent, tmp_path):
    agent.to_workspace(tmp_path / "ws")
    with pytest.raises(FileExistsError):
        agent.to_workspace(tmp_path / "ws")


def test_init_workspace_seeds_journal_and_commits_without_mutating_agent(agent, tmp_path):
    root = tmp_path / "ws"
    initialized = agent.init_workspace(root, branch="reflections", reason="Import configuration")
    assert not agent.journal.entries
    assert initialized == read_workspace(root)
    assert {entry.kind for entry in initialized.journal.entries} == {"added"}
    assert all(entry.reason == "Import configuration" for entry in initialized.journal.entries)
    assert _git(root, "branch", "--show-current").strip() == b"reflections"
    assert _git(root, "log", "-1", "--format=%s").strip() == b"Import configuration"
    assert not _git(root, "status", "--porcelain")


def test_init_workspace_resumes_saved_journal_without_committing_pending_changes(agent, tmp_path):
    root = tmp_path / "ws"
    initialized = agent.init_workspace(root)
    head = _git(root, "rev-parse", "HEAD")
    rated = Rated(
        target=agent.files().targets[0],
        revision=agent.revision,
        scale="s",
        value="helpful",
        ref="context/AGENTS.md#L1",
        item_id="item_1",
    )
    initialized.journal.record(rated)
    initialized.to_workspace(root, overwrite=True)
    _git(root, "add", "journal.jsonl")
    before = _git(root, "diff", "--cached")
    resumed = agent.init_workspace(root)
    assert resumed.journal == initialized.journal
    assert not agent.journal.entries
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "diff", "--cached") == before


@pytest.mark.parametrize("change", ["name", "version", "configuration"])
def test_init_workspace_rejects_mismatches_without_writing(agent, tmp_path, change):
    root = tmp_path / "ws"
    agent.init_workspace(root)
    changed = agent.model_copy(deep=True)
    if change == "configuration":
        changed.skills["retry"].instructions += "\nChanged\n"
    else:
        setattr(changed, change, "different")
    before = read_workspace(root)
    with pytest.raises(ValueError, match="differs"):
        changed.init_workspace(root)
    assert read_workspace(root) == before
    assert not _git(root, "status", "--porcelain")


@pytest.mark.parametrize("plain_workspace", [False, True])
def test_init_workspace_accepts_empty_directory_or_plain_workspace(agent, tmp_path, plain_workspace):
    root = tmp_path / "ws"
    agent.journal.record(*detect_changes(None, agent, reason="Earlier import"))
    if plain_workspace:
        agent.to_workspace(root)
    else:
        root.mkdir()
    initialized = agent.init_workspace(root)
    assert initialized == agent and read_workspace(root) == agent
    assert not _git(root, "status", "--porcelain")


def test_init_workspace_requires_a_version(agent, tmp_path):
    with pytest.raises(ValueError, match="versioned"):
        agent.model_copy(update={"version": None}).init_workspace(tmp_path / "ws")
    assert not (tmp_path / "ws").exists()
    assert agent.init_workspace(tmp_path / "ws").revision.startswith(f"{agent.version}@rev_")


def test_owns_hooks_is_its_own_file_and_part_of_the_revision(agent, tmp_path):
    owning = agent.model_copy(update={"owns_hooks": ["graphify *"]})
    assert owning.revision != agent.revision and owning.files().files["owned_hooks.json"] == '[\n  "graphify *"\n]\n'
    assert MetaAgent.from_workspace(owning.to_workspace(tmp_path / "ws")).owns_hooks == ["graphify *"]
    assert json.loads((tmp_path / "ws" / "owned_hooks.jsonc").read_text()) == ["graphify *"]
    assert "owns_hooks" not in json.loads((tmp_path / "ws" / "agent.json").read_text())
    owning.init_workspace(tmp_path / "git")
    with pytest.raises(ValueError, match="differs"):
        agent.init_workspace(tmp_path / "git")


# --- the journal file: append without reading, load, dump --------------------------------------------------------


def _reflection(agent):
    from thearc.reflection import Reflector

    from .test_reflection import GOOD, SCHEMA, Scripted, answer

    return Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", agent, SCHEMA)


def test_add_reflection_appends_to_the_workspace_journal(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    assert workspace.workspace == tmp_path / "ws"
    journal = tmp_path / "ws" / "journal.jsonl"
    before = journal.read_text().splitlines()
    reflection = _reflection(workspace)
    (entry,) = workspace.add_reflection(reflection)
    after = journal.read_text().splitlines()
    assert after[: len(before)] == before and len(after) == len(before) + 1  # Appended, nothing rewritten.
    assert workspace.add_reflection(reflection) == () and len(journal.read_text().splitlines()) == len(after)
    assert MetaAgent.from_workspace(tmp_path / "ws").journal.ratings() == [entry]


def test_a_configuration_only_workspace_appends_without_reading_the_journal(agent, tmp_path):
    agent.init_workspace(tmp_path / "ws")
    light = MetaAgent.from_workspace(tmp_path / "ws", journal=False)
    assert light.journal.entries == []
    reflection = _reflection(light)
    light.add_reflection(reflection)
    MetaAgent.from_workspace(tmp_path / "ws", journal=False).add_reflection(reflection)  # Replayed: a repeated line.
    loaded = MetaAgent.from_workspace(tmp_path / "ws").journal
    assert len(loaded.ratings()) == 1  # Reading collapses the repeat.


def test_add_reflection_refuses_a_workspace_holding_another_configuration(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    with (workspace.workspace / "context/AGENTS.md").open("a") as stream:
        stream.write("More.\n")  # Edited after the workspace was read.
    with pytest.raises(ValueError, match="another configuration"):
        workspace.add_reflection(_reflection(workspace))


def test_journal_load_dump_and_an_unfinished_last_line(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    path = tmp_path / "copy.jsonl"
    workspace.journal.dump(path)
    assert Journal.load(path) == workspace.journal and Journal.load(tmp_path / "missing.jsonl").entries == []
    with path.open("a") as stream:
        stream.write('{"id": "cut sh')  # An append interrupted mid-line.
    with pytest.warns(UserWarning, match="unfinished last line"):
        assert Journal.load(path) == workspace.journal
    path.write_text('{"broken"\n' + path.read_text())
    with pytest.raises(ValueError, match="invalid entry"):
        Journal.load(path)


def test_the_byte_limit_covers_configuration_not_the_journal(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    root = tmp_path / "ws"
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and ".git" not in f.parts
               and f.name not in {"journal.jsonl", ".gitattributes"})
    assert (root / "journal.jsonl").stat().st_size > 100
    assert MetaAgent.from_workspace(root, max_bytes=size).journal == workspace.journal


def test_a_workspace_repository_may_hold_its_own_files(agent, tmp_path):
    from thearc.resources import agent_at

    workspace = agent.init_workspace(tmp_path / "ws")
    root = workspace.workspace
    (root / "README.md").write_text("# My agent\n")
    (root / "LICENSE").write_bytes(b"\x89PNG not text")
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("notes\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "Docs"],
                   check=True)
    assert MetaAgent.from_workspace(root).revision == workspace.revision
    assert agent_at(root, "HEAD").revision == workspace.revision
    (root / "skills" / "retry" / "stray").mkdir(parents=True)  # Inside a configuration folder: still an error.
    (root / "skills" / "unknown.txt").write_text("x")
    with pytest.raises(ValueError, match="unexpected workspace file"):
        MetaAgent.from_workspace(root)


def test_bump_version():
    from thearc.resources import bump_version

    assert bump_version("0.9.68") == "0.9.69"
    assert bump_version("0.9.68", "minor") == "0.10.0" and bump_version("0.9.68", "major") == "1.0.0"
    assert bump_version("2") == "2.0.1"
    with pytest.raises(ValueError, match="cannot bump"):
        bump_version("1.0-beta")


def test_release_bumps_the_version_and_adds_a_branch(agent, tmp_path):
    workspace = agent.model_copy(update={"version": "0.9.68"}).init_workspace(tmp_path / "ws")
    root, before = workspace.workspace, workspace
    released = workspace.release()
    assert released.version == "0.9.69" and released.revision.startswith("0.9.69@")
    assert released.files().revision == before.files().revision  # Same content, new version.
    branches = subprocess.run(["git", "-C", str(root), "branch", "--format=%(refname:short)"], check=True,
                              capture_output=True, text=True).stdout.split()
    assert "release/0.9.69" in branches
    log = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%s", "release/0.9.69"], check=True,
                         capture_output=True, text=True).stdout.strip()
    assert log == "Release demo 0.9.69"
    assert MetaAgent.from_workspace(root).version == "0.9.69"
    with pytest.raises(ValueError, match="already exists"):
        workspace.model_copy(update={"version": "0.9.69"}).release(version="0.9.69")


def test_release_writes_ratings_and_pushes(reviewed, tmp_path):
    workspace, _ = reviewed
    root = workspace.workspace
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(remote)], check=True)
    released = workspace.release(include_scales=True, push="origin")
    branch = f"release/{released.version}"
    ratings = subprocess.run(["git", "-C", str(root), "show", f"{branch}:RATINGS.md"], check=True,
                             capture_output=True, text=True).stdout
    assert ratings.startswith(f"# {released.name} {released.version}\n")
    assert "- Limits `skills/retry/SKILL.md#L15-L17` [res_" in ratings and "] (harmful: 1)" in ratings
    assert MetaAgent.from_workspace(root).revision == released.revision  # A view, not configuration.
    pushed = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", branch], check=True,
                            capture_output=True, text=True).stdout.strip()
    local = subprocess.run(["git", "-C", str(root), "rev-parse", branch], check=True,
                           capture_output=True, text=True).stdout.strip()
    assert pushed == local
