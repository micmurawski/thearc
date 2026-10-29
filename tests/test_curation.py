"""Curation without model calls: a scripted curator edits the clone and calls the real tools."""

import json
import subprocess
import urllib.request
from contextlib import contextmanager

import pytest

from thearc.curation import CurationError, Curator
from thearc.reflection import Conversation, Reflector, Runtime
from thearc.reflection import runtimes as runtime_module
from thearc.reflection.runtimes import ToolEndpoint
from thearc.resources import MetaAgent

from .test_reflection import GOOD, SCHEMA, Scripted, answer, usefulness


def call(url: str, name: str, **arguments) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    request = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request) as response:
        result = json.loads(response.read())["result"]
    return {"error": result["isError"], "text": result["content"][0]["text"]}


class ScriptedCurator(Runtime):
    """Runs ``script(workspace, tool)`` as the curator's turn and records what it was sent and told."""

    harness = "codex"

    def __init__(self, script):
        super().__init__(model="scripted")
        self.script, self.sent, self.replies, self.edit = script, [], [], None

    @contextmanager
    def conversation(self, workspace, tool, *, fork=None, edit=False):
        runtime = self
        runtime.edit, runtime.tools = edit, tool.tools

        class Talk(Conversation):
            id = "curator-session-1"

            def send(self, message):
                runtime.sent.append(message)

                def tool_call(name, **arguments):
                    reply = call(tool.url, name, **arguments)
                    runtime.replies.append(reply)
                    return reply

                runtime.script(workspace, tool_call)
                return "done"

        yield Talk()


def head(root, ref="HEAD"):
    return subprocess.run(["git", "-C", str(root), "rev-parse", ref], check=True, capture_output=True,
                          text=True).stdout.strip()


def edit(workspace, old, new, path="skills/retry/SKILL.md"):
    file = workspace / path
    file.write_text(file.read_text().replace(old, new))


def test_a_curation_commits_each_change_on_its_own_branch_and_records_it(reviewed):
    workspace, reflection = reviewed

    def script(root, tool):
        edit(root, "max 3", "max 1, and never for permanent errors")
        assert tool("commit_change", message=f"Stop retrying permanent errors ({reflection.id}#0)")["error"] is False
        assert tool("finish", summary="Tightened the retry limit.")["error"] is False

    runtime = ScriptedCurator(script)
    curation = Curator(runtime=runtime).curate("Fix what the review found.", [reflection], workspace,
                                               scales=[usefulness])
    root = workspace.workspace
    assert runtime.edit is True and runtime.tools == ("commit_change", "finish")
    assert head(root, curation.branch) == curation.head and head(root) == curation.base  # Main branch untouched.
    (change,) = curation.commits
    assert change.changed == ("Modified skill:retry#Retry > Limits",) and "Stop retrying" in change.message
    (entry,) = curation.entries
    limits = {t.section: i for t, i in workspace.journal.ids().items()}["Retry > Limits"]
    assert entry.kind == "modified" and entry.reason.startswith("Stop retrying") and entry.touched == [limits]
    assert entry.extra["curation"] == curation.id and entry.extra["author"] == "curator (codex)"
    assert "never for permanent errors" in curation.candidate().skills["retry"].instructions
    on_branch = MetaAgent.from_workspace(root).journal  # The main branch's journal has no curation entries…
    assert not on_branch.changes()[-1].extra.get("curation")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", curation.branch], check=True)
    assert MetaAgent.from_workspace(root).journal.changes()[-1] == entry  # …the candidate's does.


def test_the_prompt_lists_items_and_ratings_by_lineage_id(reviewed):
    workspace, reflection = reviewed
    seen = {}

    def script(root, tool):
        seen["skill"] = (root / "skills/retry/SKILL.md").read_text()
        tool("finish", summary="Nothing to change.")

    runtime = ScriptedCurator(script)
    Curator(runtime=runtime).curate("Fix it.", [reflection], workspace, scales=[usefulness])
    limits = {t.section: i for t, i in workspace.journal.ids().items()}["Retry > Limits"]
    prompt = runtime.sent[0]
    assert f"`{reflection.id}#/0` usefulness **harmful** (`skills/retry/SKILL.md#L17`): {limits} " \
           f"skill:retry#Retry > Limits" in prompt
    assert "| `skills/retry/SKILL.md#L17` | harmful | The limit looped. |" in prompt and "commit_change" in prompt
    assert "```json" not in prompt
    assert f"- Limits `skills/retry/SKILL.md#L15-L17` [{limits}] (harmful: 1, neutral: 0, helpful: 0)" in prompt
    assert f"## Limits <!-- {limits} -->" in seen["skill"]  # The files carry IDs only.


def test_a_new_section_gets_its_marker_so_a_later_rename_is_a_move(agent, tmp_path):
    candidate = agent.init_workspace(tmp_path / "ws").to_worktree(tmp_path / "c")
    folder = candidate.workspace
    skill = folder / "skills/retry/SKILL.md"
    replies = []

    def curate(change, message):
        def script(root, tool):
            change(root / "skills/retry/SKILL.md")
            replies.append(tool("commit_change", message=message)["text"])
            tool("finish", summary="Done.")
        return Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [], MetaAgent.from_workspace(folder))

    curate(lambda file: file.write_text(file.read_text() + "\n## Backoff\n\nwait 2s\n"), "Add a backoff")
    added = {t.section: i for t, i in MetaAgent.from_workspace(folder).journal.ids().items()}
    backoff = added["Retry > Backoff"]
    assert f"## Backoff <!-- {backoff} -->" in skill.read_text()  # Committed with the section.
    assert f"Marked skill:retry#Retry > Backoff as {backoff}" in replies[0]
    assert "Committed" in replies[0] and "Added skill:retry#Retry > Backoff" in replies[0]

    curation = curate(lambda file: file.write_text(file.read_text().replace("## Backoff", "## Waiting")),
                      "Rename the backoff")
    (moved,) = curation.entries
    assert moved.kind == "moved" and moved.target.section == "Retry > Waiting"
    ids = {t.section: i for t, i in MetaAgent.from_workspace(folder).journal.ids().items()}
    assert ids["Retry > Waiting"] == backoff and "Retry > Backoff" not in ids  # One history, across the rename.
    assert "Marked" not in replies[1]


def test_frozen_files_are_restored_and_frozen_sections_refused(agent, tmp_path):
    frozen = agent.model_copy(update={"frozen": ["skill:retry", "context:AGENTS.md#Rules"]})
    workspace = frozen.init_workspace(tmp_path / "ws")
    replies = []

    def script(root, tool):
        edit(root, "max 3", "max 9")
        (root / "skills/retry/extra.md").write_text("# Extra\n")
        edit(root, "No emoji.", "No emoji, ever.", path="rules/style.md")
        replies.append(tool("commit_change", message="Tighten the style rule"))
        edit(root, "Be brief.", "Be verbose.", path="context/AGENTS.md")
        replies.append(tool("commit_change", message="Change the rules"))
        edit(root, "Be verbose.", "Be brief.", path="context/AGENTS.md")
        replies.append(tool("finish", summary="Done."))

    runtime = ScriptedCurator(script)
    curation = Curator(runtime=runtime).curate("Fix it.", [], workspace)
    committed, refused, finished = replies
    assert not committed["error"] and "skills/retry/SKILL.md" in committed["text"]
    assert "skills/retry/extra.md" in committed["text"] and "Modified rule:style.md" in committed["text"]
    assert refused["error"] and "context:AGENTS.md#Rules" in refused["text"] and not finished["error"]
    candidate = curation.candidate()
    assert "max 3" in candidate.skills["retry"].instructions and "extra.md" not in candidate.skills["retry"].files
    assert "`skill:retry`" in runtime.sent[0]


def test_nothing_changed_is_recorded_as_kept(reviewed):
    workspace, reflection = reviewed
    curation = Curator(runtime=ScriptedCurator(lambda root, tool: tool("finish", summary="One loop is not enough."))
                       ).curate("Fix it.", [reflection], workspace)
    (kept,) = curation.entries
    assert not curation.changed and kept.kind == "kept" and kept.target.section == "Retry > Limits"
    assert kept.reason == "One loop is not enough." and kept.extra["curation"] == curation.id
    assert curation.head != curation.base  # The Kept entry is committed on the candidate branch.


def test_protected_files_unparseable_edits_and_unfinished_work_are_refused(reviewed):
    workspace, reflection = reviewed

    def script(root, tool):
        (root / "journal.jsonl").write_text("")
        reply = tool("commit_change", message="Wipe the history")
        assert reply["error"] and "journal.jsonl may not be edited" in reply["text"] and "no edits" in reply["text"]
        assert (root / "journal.jsonl").read_text()  # Restored.
        text = (root / "skills/retry/SKILL.md").read_text()
        examples = next(line for line in text.splitlines() if line.startswith("## Examples"))
        marker = text.split("## Limits ")[1].split("\n")[0]
        edit(root, examples, f"## Examples {marker}")
        reply = tool("commit_change", message="Copy a marker")
        assert reply["error"] and "appears on both" in reply["text"]
        edit(root, f"## Examples {marker}", f"{examples} {marker}")
        reply = tool("commit_change", message="Two markers")
        assert reply["error"] and "two markers on one line" in reply["text"]
        edit(root, f"{examples} {marker}", examples)
        edit(root, "max 3", "max 2")
        reply = tool("finish", summary="Done.")
        assert reply["error"] and "skills/retry/SKILL.md" in reply["text"]
        assert not tool("commit_change", message="Lower the limit")["error"]
        assert not tool("finish", summary="Lowered the limit.")["error"]

    curation = Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [reflection], workspace)
    assert len(curation.commits) == 1


def test_a_curator_that_never_finishes_fails(reviewed):
    workspace, reflection = reviewed
    with pytest.raises(CurationError, match="without calling finish") as error:
        Curator(runtime=ScriptedCurator(lambda root, tool: None)).curate("Fix it.", [reflection], workspace)
    assert error.value.code == "no_finish"


def test_reflections_must_be_in_the_journal_first(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", workspace, SCHEMA)
    with pytest.raises(ValueError, match="add_reflection first"):
        Curator(runtime=ScriptedCurator(lambda root, tool: None)).curate("Fix it.", [reflection], workspace)


def test_cli_runtimes_can_edit_when_asked(monkeypatch, tmp_path):
    calls = []
    outputs = [json.dumps({"subtype": "success", "is_error": False, "result": "ok", "session_id": "s"}),
               json.dumps({"type": "message_end", "message": {"role": "assistant", "content": []}})]
    monkeypatch.setattr(runtime_module, "_run", lambda command, **kw: calls.append(command) or outputs.pop(0))
    tools = ToolEndpoint("http://127.0.0.1:1/x/mcp", ("commit_change", "finish"))
    with runtime_module.ClaudeRuntime().conversation(tmp_path, tools, edit=True) as talk:
        talk.send("curate")
    with runtime_module.PiRuntime().conversation(tmp_path, tools, edit=True) as talk:
        talk.send("curate")
    claude, pi = calls
    assert claude[claude.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write"
    assert claude[claude.index("--allowedTools") + 1] == (
        "mcp__thearc__commit_change,mcp__thearc__finish,Edit,Write")
    assert pi[pi.index("--tools") + 1] == (
        "read,grep,find,ls,edit,write,mcp__thearc__commit_change,mcp__thearc__finish")


def test_a_runtime_started_inside_a_harness_session_is_not_nested(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("KEEP_ME", "yes")
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    assert runtime_module._run(["claude", "-p"], cwd=tmp_path, timeout=5) == "ok"
    assert "CLAUDECODE" not in seen["env"] and seen["env"]["KEEP_ME"] == "yes"
