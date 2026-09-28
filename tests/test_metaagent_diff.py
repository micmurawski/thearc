import pytest
from click.testing import CliRunner

from thearc import MCP, ContextDocument, Hook, MetaAgent, Skill
from thearc.cli import main
from thearc.learning.curation.epochs import commit_epoch
from thearc.models import Ranks


def config():
    return MetaAgent(name="graphify", version="1.0", skills=[
        Skill(name="graphify", instructions="# Query\n\nQuery first.\n",
              files={"references/query.md": "Broad query\n"}),
    ], context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nRead source.\n")])


def test_diff_content_and_no_mutation(tmp_path):
    before = config()
    after = before.model_copy(deep=True)
    after.skills["graphify"].files["references/query.md"] = "Focused query\n"
    saved = before.model_dump(), after.model_dump()
    diff = before.diff(after)
    assert "--- a/skills/graphify/files/references/query.md" in diff
    assert "-Broad query\n+Focused query\n" in diff
    assert "agent.json" not in diff
    assert (before.model_dump(), after.model_dump()) == saved
    assert not list(tmp_path.iterdir())
    assert before.diff(before) == ""


def test_ranks_and_release_filters():
    before = config()
    after = before.model_copy(deep=True)
    after.version = "1.1"
    after.skills["graphify"].ranks = Ranks(helpful=3)
    after.context["AGENTS.md"].content = "# Rules (harmful: 0, neutral: 0, helpful: 2)\n\nRead source.\n"
    assert before.diff(after, include_version=False) == ""
    assert ".graphify_version" in before.diff(after)
    assert '"helpful": 3' in before.diff(after, include_ranks=True)
    assert "helpful: 2" in before.diff(after, include_ranks=True)
    after.skills["graphify"].files[".graphify_version"] = "upstream-version"
    assert "upstream-version" in before.diff(after, include_version=False)


def test_add_remove_empty_files_and_structured_config():
    before = config()
    before.skills["graphify"].files["empty.txt"] = ""
    after = before.model_copy(deep=True)
    del after.skills["graphify"].files["empty.txt"]
    after.skills["graphify"].files["new.txt"] = ""
    after.hooks["guard"] = Hook(name="guard", command="echo guard")
    after.mcps["local"] = MCP(name="local", command="example")
    del after.context["AGENTS.md"]
    diff = before.diff(after)
    assert "--- a/skills/graphify/files/empty.txt\n+++ /dev/null" in diff
    assert "--- /dev/null\n+++ b/skills/graphify/files/new.txt" in diff
    assert "--- a/context/AGENTS.md\n+++ /dev/null" in diff
    assert '"command": "echo guard"' in diff
    assert '"command": "example"' in diff


def test_order_newline_and_context():
    before = config()
    before.skills["graphify"].files = {"a.txt": "a\nb\nc\nd\n", "b.txt": ""}
    after = before.model_copy(deep=True)
    after.skills["graphify"].files = dict(reversed(list(after.skills["graphify"].files.items())))
    assert before.diff(after, include_ranks=True) == ""
    after.skills["graphify"].files["a.txt"] = "a\nb\nc\nd"
    diff = before.diff(after, context_lines=0)
    assert "-d\n+d\n\\ No newline at end of file\n" in diff
    assert " a\n" not in diff


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_context(value):
    with pytest.raises(ValueError, match="context_lines"):
        config().diff(config(), context_lines=value)


def test_cli_verified_epoch_diff(tmp_path):
    before = config()
    after = before.model_copy(deep=True)
    after.skills["graphify"].files["references/query.md"] = "Focused query\n"
    commit_epoch(tmp_path, epoch_id="first", expected_head=None, baseline=before, agent=after,
                 ledger={}, reflections=[], changes=[], actor={}, summary="Focused query")
    runner = CliRunner()
    for options in ([], ["--epoch", "first", "--context-lines", "0"]):
        result = runner.invoke(main, ["curation", "diff", str(tmp_path), *options])
        assert result.exit_code == 0, result.output
        assert "-Broad query\n+Focused query\n" in result.output
    record = tmp_path / "epochs/first.json"
    record.write_text(record.read_text().replace("Focused query", "Tampered query"))
    result = runner.invoke(main, ["curation", "diff", str(tmp_path)])
    assert result.exit_code == 1
    assert "hash mismatch" in result.output


def test_cli_noop_filters_and_missing_epoch(tmp_path):
    runner = CliRunner()
    assert runner.invoke(main, ["curation", "diff", str(tmp_path)]).exit_code == 1
    before = config()
    after = before.model_copy(deep=True)
    after.version = "1.1"
    after.skills["graphify"].ranks = Ranks(helpful=2)
    commit_epoch(tmp_path, epoch_id="first", expected_head=None, baseline=before, agent=after,
                 ledger={}, reflections=[], changes=[], actor={}, summary="Metadata only")
    result = runner.invoke(main, ["curation", "diff", str(tmp_path), "--no-version"])
    assert result.exit_code == 0
    assert "No differences" in result.output
    result = runner.invoke(main, ["curation", "diff", str(tmp_path), "--include-ranks"])
    assert result.exit_code == 0
    assert '"helpful": 2' in result.output
