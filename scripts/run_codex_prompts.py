#!/usr/bin/env python3
"""Collect a verified Codex session per prompt; stop the batch on its first failure.

Each prompt runs in a retained detached Git worktree at the same pinned commit.
The source checkout's files, index, and branch are left alone. Uncommitted and
ignored source files are not copied. Raw artifacts may contain sensitive content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from thearc import MetaAgent
from thearc.learning import SearchFilters, SessionStore, SourceConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"}


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def extract_prompt(target: str) -> str:
    """Resolve explicit Markdown/text files; never silently send a missing filename."""
    path = Path(target).expanduser()
    try:
        if not path.is_file() and not path.is_absolute():
            path = REPO_ROOT / path
        is_file = path.is_file()
    except OSError:
        is_file = False
    if is_file:
        content = path.read_text(encoding="utf-8")
        match = re.search(r"^##\s+Agent Prompt\s*\n+```(?:text)?\n(.*?)\n```", content, re.MULTILINE | re.DOTALL)
        return (match.group(1) if match else content).strip()
    if "\n" not in target and target.endswith((".md", ".txt")):
        raise ValueError(f"Prompt file does not exist: {target}")
    return target.strip()


def inspect_stream(path: Path) -> dict:
    threads, tools, messages, errors = set(), set(), set(), []
    completed, failed, malformed = False, False, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise TypeError("Expected event object")
        except (ValueError, TypeError):
            malformed = True
            continue
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str) and event["thread_id"]:
            threads.add(event["thread_id"])
        elif kind == "turn.completed":
            completed = True
        elif kind == "turn.failed":
            failed = True
            errors.append(event.get("error"))
        elif kind == "error":
            errors.append(event.get("message"))
        elif kind == "item.completed":
            item = event.get("item", {})
            if not isinstance(item, dict):
                malformed = True
                continue
            identity = item.get("id", json.dumps(item, sort_keys=True))
            if item.get("type") in TOOL_ITEMS:
                tools.add(identity)
            if item.get("type") == "agent_message" and str(item.get("text") or "").strip():
                messages.add(identity)
    return {"thread_id": next(iter(threads)) if len(threads) == 1 else None,
            "turn_completed": completed, "turn_failed": failed, "malformed_stream": malformed,
            "tool_items": len(tools), "assistant_messages": len(messages), "errors": errors}


def find_session(sessions: Path, thread_id: str | None, cwd: Path) -> Path | None:
    """Match the actual thread AND project, not timestamps or the most recent log."""
    if not thread_id or not re.fullmatch(r"[a-zA-Z0-9_-]+", thread_id):
        return None
    matches = []
    for path in sessions.rglob(f"rollout-*{thread_id}.jsonl"):
        with path.open(encoding="utf-8") as stream:
            try:
                header = json.loads(next(stream))
            except (ValueError, StopIteration):
                continue
        payload = header.get("payload", {})
        if (header.get("type") == "session_meta" and payload.get("id") == thread_id
                and Path(payload.get("cwd", "")).resolve() == cwd.resolve()):
            matches.append(path)
    return matches[0] if len(matches) == 1 else None


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def collect(prompt: str, label: str, args: argparse.Namespace, output: Path) -> dict:
    run_id = uuid4().hex
    run_dir = output / "runs" / run_id
    run_dir.mkdir(parents=True)
    worktree = output / "worktrees" / run_id
    git(args.cwd, "worktree", "add", "--detach", str(worktree), args.base_commit)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    save(run_dir / "agent-before.json", MetaAgent.from_project("codex", worktree).model_dump(mode="json"))
    command = [args.codex_bin, "--sandbox", "workspace-write", "--ask-for-approval", "never",
               "exec", "--json", "--color", "never", "--output-last-message", str(run_dir / "response.md")]
    if args.model:
        command.extend(["--model", args.model])
    command.append("-")  # stdin avoids accidental CLI-option interpretation and shell interpolation.
    result = {"prompt": label, "cwd": str(worktree), "source_checkout": str(args.cwd),
              "base_commit": args.base_commit, "run_dir": str(run_dir),
              "started_at": datetime.now(timezone.utc).isoformat(), "command": command,
              "git_head": git(worktree, "rev-parse", "HEAD"),
              "git_status_before": git(worktree, "status", "--porcelain"), "status": "running"}
    save(run_dir / "manifest.json", result)
    interrupted = None
    with (run_dir / "events.jsonl").open("w") as stdout, (run_dir / "stderr.log").open("w") as stderr:
        try:
            with subprocess.Popen(command, cwd=worktree, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                  text=True, start_new_session=True) as process:
                try:
                    process.communicate(prompt, timeout=args.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    interrupted = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                result["exit_code"] = process.returncode
        except OSError as exc:
            interrupted = f"launch_error: {exc}"
            result["exit_code"] = None
    result.update(inspect_stream(run_dir / "events.jsonl"))
    transcript = find_session(args.sessions_dir, result["thread_id"], worktree)
    result["transcript"] = None
    result["indexed_activity_events"] = 0
    result["indexed_tool_events"] = 0
    if transcript:
        destination = output / "sessions" / transcript.name
        # Never overwrite another captured run, even if a misconfigured CLI reuses a thread.
        with transcript.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target)
        result["transcript"] = str(destination)
        with SessionStore(output / "index.sqlite") as history:
            history.register_source(SourceConfig(id="codex-prompts", harness="codex", root=output / "sessions"))
            history.sync()
            sessions = history.list_sessions(SearchFilters(source_ids=["codex-prompts"]), limit=1000)
            session = next((s for s in sessions if s.native_id == result["thread_id"]), None)
            if session:
                events = list(history.iter_events(SearchFilters(session_ids=[session.id])))
                result["indexed_tool_events"] = sum(e.kind in {"tool_call", "tool_result"} for e in events)
                result["indexed_activity_events"] = sum(
                    e.kind in {"tool_call", "tool_result"} or
                    (e.kind == "message" and e.role == "assistant" and bool(e.text.strip())) for e in events
                )
    activity = result["tool_items"] > 0 or result["assistant_messages"] > 0
    reason = interrupted
    if not reason:
        if result["exit_code"] != 0 or result["turn_failed"] or not result["turn_completed"]:
            reason = "agent_execution_failed"
        elif result["malformed_stream"]:
            reason = "invalid_event_stream"
        elif not transcript:
            reason = "matching_transcript_missing"
        elif not activity:
            reason = "no_assistant_or_tool_activity"
        elif not result["indexed_activity_events"]:
            reason = "no_indexed_activity"
        elif not args.allow_response_only and (not result["tool_items"] or not result["indexed_tool_events"]):
            reason = "no_tool_activity"
    result.update(status="failed" if reason else "completed", failure_reason=reason,
                  has_activity=activity, finished_at=datetime.now(timezone.utc).isoformat(),
                  git_status_after=git(worktree, "status", "--porcelain"))
    # Include staged and committed tracked edits relative to the pinned baseline.
    # Untracked outputs remain available in the retained worktree itself.
    patch = subprocess.run(["git", "diff", "--binary", args.base_commit], cwd=worktree,
                           check=True, capture_output=True).stdout
    (run_dir / "changes.patch").write_bytes(patch)
    save(run_dir / "manifest.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompts", nargs="+", help="Prompt files or quoted task text, executed sequentially")
    parser.add_argument("-C", "--cwd", type=Path, default=REPO_ROOT / "tests" / "fastapi")
    parser.add_argument("--base", default="HEAD", help="Commit used for every isolated worktree (default: source HEAD)")
    parser.add_argument("-m", "--model")
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--sessions-dir", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--allow-response-only", action="store_true", help="Do not require tools for a successful run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.cwd = args.cwd.expanduser().resolve()
    args.sessions_dir = args.sessions_dir.expanduser().resolve()
    try:
        prompts = [extract_prompt(target) for target in args.prompts]
        if not all(prompts):
            raise ValueError("Prompts must not be empty")
        if args.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if Path(git(args.cwd, "rev-parse", "--show-toplevel")).resolve() != args.cwd:
            raise ValueError("--cwd must be a checkout root; initialize tests/fastapi with git submodule update --init")
        args.base_commit = git(args.cwd, "rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}")
        output = (args.output or REPO_ROOT / "logs" / "codex-prompts" / uuid4().hex).expanduser().resolve()
        if output == args.cwd or args.cwd in output.parents:
            raise ValueError("Output must be outside the target checkout")
        if output == args.sessions_dir or args.sessions_dir in output.parents:
            raise ValueError("Output must be outside the live session directory")
        if args.dry_run:
            print(json.dumps({"cwd": str(args.cwd), "prompts": args.prompts, "output": str(output),
                              "base_commit": args.base_commit,
                              "mode": "isolated detached worktree per prompt; stop on first failure"}, indent=2))
            return 0
        if not shutil.which(args.codex_bin):
            raise ValueError(f"Codex executable not found: {args.codex_bin}")
        output.mkdir(parents=True, exist_ok=False)
        (output / "sessions").mkdir()
        summary = {"requested_prompts": args.prompts, "runs": [], "status": "running",
                   "source_checkout": str(args.cwd), "base_commit": args.base_commit,
                   "not_started": list(args.prompts)}
        save(output / "summary.json", summary)
        for label, prompt in zip(args.prompts, prompts):
            print(f"Running {label[:100]} (artifacts: {output})", flush=True)
            try:
                result = collect(prompt, label, args, output)
            except (OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt) as exc:
                summary.update(status="failed", runner_error=type(exc).__name__)
                save(output / "summary.json", summary)
                raise
            summary["runs"].append(result)
            summary["not_started"] = args.prompts[len(summary["runs"]):]
            summary["status"] = "running" if result["status"] == "completed" else "failed"
            save(output / "summary.json", summary)
            print(f"{result['status']}: {result['failure_reason'] or result['thread_id']}", flush=True)
            if result["status"] != "completed":
                print(f"Batch stopped. Inspect {result['run_dir']}/manifest.json and stderr.log", file=sys.stderr)
                return 1
        summary["status"] = "completed"
        save(output / "summary.json", summary)
        print(f"Collected {len(summary['runs'])} verified sessions in {output / 'sessions'}")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    sys.exit(main())
