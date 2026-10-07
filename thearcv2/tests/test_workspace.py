import json

import pytest

from thearcv2.resources import MetaAgent, detect_changes, read_workspace, write_workspace
from thearcv2.resources.journal import Rated


def test_round_trip_with_journal(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws")
    assert read_workspace(root) == agent


def test_without_journal_for_reflection(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws", journal=False)
    assert not (root / "journal").exists()
    assert len(read_workspace(root).journal) == 0


def test_hand_edited_skill_is_read(agent, tmp_path):
    root = write_workspace(agent, tmp_path / "ws")
    path = root / "skills/retry/SKILL.md"
    path.write_text(path.read_text().replace("max 3", "max 5"))
    assert "max 5" in read_workspace(root).skills["retry"].instructions


def test_overwrite_syncs_config_and_keeps_journal(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws")
    (root / ".git").mkdir()
    smaller = agent.model_copy(deep=True)
    smaller.resources = {}
    smaller.journal.entries = smaller.journal.entries[:1]
    write_workspace(smaller, root, overwrite=True)
    assert not (root / "rules").exists() and (root / ".git").is_dir()
    assert len(read_workspace(root).journal) == len(agent.journal)


def test_journal_entries_cannot_be_rewritten(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws")
    rewritten = agent.model_copy(deep=True)
    rewritten.journal.entries[0] = rewritten.journal.entries[0].model_copy(update={"revision": "rev_x"})
    with pytest.raises(ValueError, match="differs from the recorded one"):
        write_workspace(rewritten, root, overwrite=True)


def test_journal_is_one_jsonl_file_with_union_merge(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws")
    lines = (root / "journal.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [entry.id for entry in agent.journal.entries]
    assert (root / ".gitattributes").read_text() == "journal.jsonl merge=union\n"
    write_workspace(agent, root, overwrite=True)
    assert (root / ".gitattributes").read_text() == "journal.jsonl merge=union\n"


def test_union_merged_journal_is_read(agent, human, tmp_path):
    rated = Rated(target=agent.files().targets[0], revision=agent.revision, extra=human, scale="s", value="x",
                  ref="context/AGENTS.md#L1", item_id="item_1")
    agent.journal.record(*detect_changes(None, agent, **human), rated)
    root = write_workspace(agent, tmp_path / "ws")
    path = root / "journal.jsonl"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join([lines[-1], *lines, lines[0]]))  # Out of order and duplicated, as git may leave it.
    assert read_workspace(root).journal == agent.journal
    path.write_text(path.read_text() + lines[0].replace('"revision":"', '"revision":"x'))
    with pytest.raises(ValueError, match="differs"):
        read_workspace(root)


def test_reflection_export_leaves_the_recorded_journal_alone(agent, human, tmp_path):
    agent.journal.record(*detect_changes(None, agent, **human))
    root = write_workspace(agent, tmp_path / "ws")
    before = (root / "journal.jsonl").read_text()
    write_workspace(agent.model_copy(update={"journal": type(agent.journal)()}), root, journal=False, overwrite=True)
    assert (root / "journal.jsonl").read_text() == before


@pytest.mark.parametrize("mutate", [
    lambda root: (root / "stray.txt").write_text("x"),
    lambda root: (root / "skills/empty").mkdir() or (root / "skills/empty/notes.md").write_text("x"),
    lambda root: (root / "hooks.json").write_text(json.dumps({"guard": {"bogus": 1}})),
    lambda root: (root / "agent.json").write_text(json.dumps({"schema_version": 1, "name": "a", "version": None})),
])
def test_invalid_workspaces_are_rejected(agent, tmp_path, mutate):
    root = write_workspace(agent, tmp_path / "ws")
    mutate(root)
    with pytest.raises(ValueError):
        read_workspace(root)


def test_refuses_existing_directory_without_overwrite(agent, tmp_path):
    write_workspace(agent, tmp_path / "ws")
    with pytest.raises(FileExistsError):
        MetaAgent.to_workspace(agent, tmp_path / "ws")
