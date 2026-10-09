"""CLI commands and native transcript capture for the prompt collector."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID

from thearc import MetaAgent
from thearc.models.agent import ContextDocument, Skill


def snapshot_agent(args, worktree: Path) -> MetaAgent:
    if args.agent_snapshot:
        return MetaAgent.model_validate_json(args.agent_snapshot.expanduser().read_text())
    if args.harness != "opencode":
        return MetaAgent.from_project(args.harness, worktree)
    # OpenCode has no installer in thearc yet. Capture project skills/instructions;
    # use --agent-snapshot when the canonical producer includes other resources.
    skills = {}
    for root in (worktree / ".agents/skills", worktree / ".opencode/skills"):
        for path in sorted(root.glob("*/SKILL.md")):
            skill = Skill.from_dir(path.parent)
            skills[skill.name] = skill
    return MetaAgent(
        name="opencode-project",
        skills=skills,
        context=[
            ContextDocument.from_file(worktree / name)
            for name in ("AGENTS.md", "CLAUDE.md")
            if (worktree / name).is_file()
        ],
    )


def command_for(args, run_dir: Path) -> list[str]:
    if args.harness == "codex":
        command = [
            args.agent_bin,
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "exec",
            "--json",
            "--color",
            "never",
            "--output-last-message",
            str(run_dir / "response.md"),
        ]
    elif args.harness == "pi":
        command = [args.agent_bin, "--print", "--mode", "json", "--session-dir", str(run_dir / "native")]
    elif args.harness == "claude":
        command = [
            args.agent_bin,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--session-id",
            str(UUID(run_dir.name)),
        ]
    else:
        # A private server keeps timeout termination scoped to this run.
        command = [args.agent_bin, "run", "--standalone", "--format", "json"]
    if args.model:
        command.extend(["--model", args.model])
    command.extend(args.agent_arg)
    if args.harness == "codex":
        command.append("-")
    return command


def inspect_output(harness: str, run_dir: Path) -> dict:
    sessions, tools, messages, errors = set(), set(), [], []
    completed = malformed = failed = False
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise TypeError("Expected object")
            kind = event.get("type")
            session = event.get("session_id") if harness == "claude" else event.get("sessionID")
            if harness == "pi" and kind == "session":
                session = event.get("id")
            if session:
                sessions.add(session)
            if kind == "error":
                errors.append(event.get("error", event.get("message")))
                failed = True
            if harness == "claude" and kind == "result":
                completed = event.get("subtype") == "success" and not event.get("is_error")
                failed = not completed
                if failed:
                    errors.append(event.get("errors", event.get("result")))
            if harness == "pi" and kind in {"agent_end", "agent_settled"}:
                completed = not event.get("willRetry", False)
            message = event.get("message", {})
            if (harness == "claude" and kind == "assistant") or (
                harness == "pi" and kind == "message_end" and message.get("role") == "assistant"
            ):
                # The last assistant message decides whether Pi recovered from an earlier provider error.
                if harness == "pi":
                    failed = message.get("stopReason") in {"error", "aborted"}
                    if failed:
                        errors.append(message.get("errorMessage", message.get("stopReason")))
                for block in message.get("content", []):
                    if block.get("type") in {"tool_use", "toolCall"}:
                        tools.add(block.get("id") or json.dumps(block, sort_keys=True))
                    if block.get("type") == "text" and block.get("text", "").strip():
                        messages.append(block["text"])
            if harness == "opencode":
                part = event.get("part", {})
                if kind == "tool_use":
                    tools.add(part.get("id") or json.dumps(part, sort_keys=True))
                if kind == "text" and part.get("text", "").strip():
                    messages.append(part["text"])
                # v2 may omit step_finish: the export is checked for final completion below.
        except (ValueError, TypeError, AttributeError):
            malformed = True
    (run_dir / "response.md").write_text("\n\n".join(messages), encoding="utf-8")
    return {
        "thread_id": next(iter(sessions)) if len(sessions) == 1 else None,
        "turn_completed": completed,
        "turn_failed": failed,
        "malformed_stream": malformed,
        "tool_items": len(tools),
        "assistant_messages": len(messages),
        "errors": errors,
    }


def capture_session(args, session_id: str | None, worktree: Path, run_dir: Path) -> Path | None:
    if not session_id or not re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
        return None
    if args.harness == "opencode":
        path = run_dir / "session.json"
        with path.open("w") as stdout, (run_dir / "export-stderr.log").open("w") as stderr:
            subprocess.run(
                [args.agent_bin, "session", "export", "--standalone", session_id],
                cwd=worktree,
                env={**os.environ, "PWD": str(worktree)},
                pass_fds=(args.lock_fd,),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=True,
                timeout=60,
            )
        data = json.loads(path.read_text())
        info = data.get("info", {})
        directory = info.get("location", {}).get("directory", info.get("directory"))
        if info.get("id") != session_id or not directory or Path(directory).resolve() != worktree.resolve():
            raise ValueError(
                f"Exported session does not match requested session/worktree: {info.get('id')}, {directory}"
            )
        # Keep the original export and a lossless single-record JSONL copy for the index.
        transcript = run_dir / f"{session_id}.jsonl"
        transcript.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        return transcript
    matches = []
    root = run_dir / "native" if args.harness == "pi" else args.sessions_dir
    for path in root.rglob(f"*{session_id}*.jsonl"):
        if "subagents" in path.parts:
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                identity = record.get("id") if args.harness == "pi" else record.get("sessionId")
                if identity == session_id and record.get("cwd") and Path(record["cwd"]).resolve() == worktree.resolve():
                    matches.append(path)
                    break
    return matches[0] if len(matches) == 1 else None


def opencode_completed(transcript: Path) -> bool:
    """Require a completed final assistant message, not a completed tool step."""
    data = json.loads(transcript.read_text())
    assistants = [
        m.get("info", m)
        for m in data.get("messages", [])
        if m.get("info", {}).get("role", m.get("type")) == "assistant"
    ]
    if not assistants:
        return False
    last = assistants[-1]
    return bool(
        data.get("info", {}).get("outcome", "succeeded") == "succeeded"
        and last.get("time", {}).get("completed")
        and not last.get("error")
        and last.get("finish") not in {None, "tool-calls", "unknown", "length", "error"}
    )
