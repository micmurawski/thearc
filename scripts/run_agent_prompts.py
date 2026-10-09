#!/usr/bin/env python3
"""Collect a verified agent session per prompt; stop the batch on its first failure.

Each prompt runs in a retained detached Git worktree at the same pinned commit.
The source checkout's files, index, and branch are left alone. Uncommitted and
ignored source files are not copied. Raw artifacts may contain sensitive content.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.prompt_harnesses import (
    capture_session,
    command_for,
    inspect_output,
    opencode_completed,
    snapshot_agent,
)
from thearc.learning.sessions.models import SearchFilters, SourceConfig
from thearc.learning.sessions.store import SessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"}


def save(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


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
    for line in path.read_text(encoding="utf-8").split("\n"):
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
    return {
        "thread_id": next(iter(threads)) if len(threads) == 1 else None,
        "turn_completed": completed,
        "turn_failed": failed,
        "malformed_stream": malformed,
        "tool_items": len(tools),
        "assistant_messages": len(messages),
        "errors": errors,
    }


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
        if (
            header.get("type") == "session_meta"
            and payload.get("id") == thread_id
            and Path(payload.get("cwd", "")).resolve() == cwd.resolve()
        ):
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
    save(run_dir / "agent-before.json", snapshot_agent(args, worktree).model_dump(mode="json"))
    command = command_for(args, run_dir)
    result = {
        "harness": args.harness,
        "prompt": label,
        "prompt_index": args.prompt_index,
        "cwd": str(worktree),
        "source_checkout": str(args.cwd),
        "base_commit": args.base_commit,
        "run_dir": str(run_dir),
        "started_at": datetime.now(UTC).isoformat(),
        "command": command,
        "git_head": git(worktree, "rev-parse", "HEAD"),
        "git_status_before": git(worktree, "status", "--porcelain"),
        "status": "running",
    }
    save(run_dir / "manifest.json", result)
    interrupted = None
    with (run_dir / "events.jsonl").open("w") as stdout, (run_dir / "stderr.log").open("w") as stderr:
        try:
            with subprocess.Popen(
                command,
                cwd=worktree,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                env={**os.environ, "PWD": str(worktree)},
                pass_fds=(args.lock_fd,),
                text=True,
                start_new_session=True,
            ) as process:
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
    result.update(
        inspect_stream(run_dir / "events.jsonl") if args.harness == "codex" else inspect_output(args.harness, run_dir)
    )
    try:
        transcript = (
            find_session(args.sessions_dir, result["thread_id"], worktree)
            if args.harness == "codex"
            else capture_session(args, result["thread_id"], worktree, run_dir)
        )
        if transcript and args.harness == "opencode":
            result["turn_completed"] = opencode_completed(transcript)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as exc:
        transcript = None
        result["capture_error"] = str(exc)
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
            history.register_source(
                SourceConfig(id=f"{args.harness}-prompts", harness=args.harness, root=output / "sessions")
            )
            history.sync()
            with closing(history.iter_sessions(SearchFilters(source_ids=[f"{args.harness}-prompts"]))) as sessions:
                session = next((s for s in sessions if s.native_id == result["thread_id"]), None)
            if session:
                events = list(history.iter_events(SearchFilters(session_ids=[session.id])))
                result["indexed_tool_events"] = sum(e.kind in {"tool_call", "tool_result"} for e in events)
                result["indexed_activity_events"] = sum(
                    e.kind in {"tool_call", "tool_result"}
                    or (e.kind == "message" and e.role == "assistant" and bool(e.text.strip()))
                    for e in events
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
    result.update(
        status="failed" if reason else "completed",
        failure_reason=reason,
        has_activity=activity,
        finished_at=datetime.now(UTC).isoformat(),
        git_status_after=git(worktree, "status", "--porcelain"),
    )
    # Include staged and committed tracked edits relative to the pinned baseline.
    # Untracked outputs remain available in the retained worktree itself.
    patch = subprocess.run(
        ["git", "diff", "--binary", args.base_commit], cwd=worktree, check=True, capture_output=True
    ).stdout
    (run_dir / "changes.patch").write_bytes(patch)
    save(run_dir / "manifest.json", result)
    return result


def refresh_summary(summary: dict) -> set[int]:
    """Keep all attempts; completion is tracked per original prompt occurrence."""
    attempted = {run["prompt_index"] for run in summary["runs"]}
    completed = {run["prompt_index"] for run in summary["runs"] if run["status"] == "completed"}
    summary["not_started"] = [p for i, p in enumerate(summary["requested_prompts"]) if i not in attempted]
    summary["pending_prompts"] = [p for i, p in enumerate(summary["requested_prompts"]) if i not in completed]
    return completed


def resume_batch(output: Path, args, prompts: list[str]) -> dict:
    """Reconcile manifests written before a killed collector could update summary.json."""
    summary = json.loads((output / "summary.json").read_text())
    if Path(summary["source_checkout"]).resolve() != args.cwd:
        raise ValueError("--cwd must match the original batch's source checkout")
    requested = summary["requested_prompts"]
    if any(label not in requested for label in args.prompts):
        raise ValueError("--resume accepts only prompts from the original batch")
    if args.base:
        commit = git(args.cwd, "rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}")
        if commit != summary["base_commit"]:
            raise ValueError("--base must match the original batch's pinned commit")
    args.base_commit = summary["base_commit"]
    runs = {Path(run["run_dir"]).name: dict(run) for run in summary["runs"]}
    for path in sorted((output / "runs").glob("*/manifest.json")):
        run = json.loads(path.read_text())
        runs[path.parent.name] = {**runs.get(path.parent.name, {}), **run}
    summary["runs"] = sorted(runs.values(), key=lambda run: run["started_at"])
    assigned = set()
    selected = dict(zip(args.prompts, prompts))
    for run in summary["runs"]:
        if run.get("harness", "codex") != args.harness or run["base_commit"] != args.base_commit:
            raise ValueError("Resume harness and baseline must match the existing batch")
        # Older collectors had no prompt_index and made at most one attempt per occurrence.
        if "prompt_index" not in run:
            candidates = [i for i, label in enumerate(requested) if label == run["prompt"] and i not in assigned]
            if not candidates:
                raise ValueError("Cannot identify an older run's prompt occurrence")
            run["prompt_index"] = candidates[0]
        index = run["prompt_index"]
        if not 0 <= index < len(requested) or requested[index] != run["prompt"]:
            raise ValueError("Run does not match the batch's original prompt list")
        assigned.add(index)
        if run["prompt"] in selected:
            recorded = (output / "runs" / Path(run["run_dir"]).name / "prompt.txt").read_text()
            if recorded != selected[run["prompt"]]:
                raise ValueError(f"Prompt changed since the original attempt: {run['prompt']}; use a new batch")
        if run["status"] == "completed" and not Path(run.get("transcript") or "").is_file():
            raise ValueError(f"Completed run's transcript is missing: {run['run_dir']}")
    refresh_summary(summary)
    return summary


def main(argv: list[str] | None = None, *, default_harness: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--harness",
        choices=["codex", "pi", "claude", "opencode"],
        default=default_harness,
        required=default_harness is None,
    )
    parser.add_argument("--bin", dest="agent_bin", help="Agent executable path")
    parser.add_argument("--agent-snapshot", type=Path, help="Optional canonical MetaAgent JSON for this producer")
    parser.add_argument(
        "--agent-arg",
        action="append",
        default=[],
        help="Extra CLI argument; repeat as --agent-arg=VALUE (for permissions, provider, etc.)",
    )
    parser.add_argument("prompts", nargs="+", help="Prompt files or quoted task text, executed sequentially")
    parser.add_argument("-C", "--cwd", type=Path, default=REPO_ROOT / "tests" / "fastapi")
    parser.add_argument("--base", help="Commit for worktrees (default: source HEAD, or original commit on resume)")
    parser.add_argument("-m", "--model", help="Model ID (required for OpenCode)")
    parser.add_argument("--codex-bin", help=argparse.SUPPRESS)
    parser.add_argument("--sessions-dir", type=Path, help="Native session root for Codex or Claude")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true", help="Reuse --output, skipping completed prompts")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--allow-response-only", action="store_true", help="Do not require tools for a successful run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.cwd = args.cwd.expanduser().resolve()
    args.agent_bin = (
        args.agent_bin
        or args.codex_bin
        or shutil.which(args.harness)
        or (str(Path.home() / ".opencode/bin/opencode") if args.harness == "opencode" else args.harness)
    )
    default_sessions = (
        Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"
        if args.harness == "claude"
        else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    )
    args.sessions_dir = (args.sessions_dir or default_sessions).expanduser().resolve()
    lock = None
    try:
        if args.resume and not args.output:
            raise ValueError("--resume requires --output pointing to an existing batch")
        if args.harness == "opencode" and not args.model:
            raise ValueError(
                "OpenCode collection requires --model provider/model[#variant]; "
                "headless defaults can differ from the interactive model selection"
            )
        prompts = [extract_prompt(target) for target in args.prompts]
        if not all(prompts):
            raise ValueError("Prompts must not be empty")
        if args.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if Path(git(args.cwd, "rev-parse", "--show-toplevel")).resolve() != args.cwd:
            raise ValueError("--cwd must be a checkout root; initialize tests/fastapi with git submodule update --init")
        args.base_commit = git(
            args.cwd, "rev-parse", "--verify", "--end-of-options", f"{args.base or 'HEAD'}^{{commit}}"
        )
        output = (args.output or REPO_ROOT / "logs" / f"{args.harness}-prompts" / uuid4().hex).expanduser().resolve()
        if output == args.cwd or args.cwd in output.parents:
            raise ValueError("Output must be outside the target checkout")
        if output == args.sessions_dir or args.sessions_dir in output.parents:
            raise ValueError("Output must be outside the live session directory")
        summary = (
            resume_batch(output, args, prompts)
            if args.resume
            else {
                "requested_prompts": args.prompts,
                "runs": [],
                "status": "running",
                "source_checkout": str(args.cwd),
                "base_commit": args.base_commit,
            }
        )
        completed = refresh_summary(summary)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "harness": args.harness,
                        "cwd": str(args.cwd),
                        "prompts": args.prompts,
                        "output": str(output),
                        "base_commit": args.base_commit,
                        "pending_prompts": [
                            p
                            for i, p in enumerate(summary["requested_prompts"])
                            if p in args.prompts and i not in completed
                        ],
                        "completed_prompts": len(completed),
                        "mode": "isolated detached worktree per prompt; stop on first failure",
                    },
                    indent=2,
                )
            )
            return 0
        if not shutil.which(args.agent_bin):
            raise ValueError(f"Agent executable not found: {args.agent_bin}")
        if output.exists() and not args.resume:
            raise ValueError(
                f"Output already exists: {output}. Use --resume to retry unfinished prompts, or choose a new --output"
            )
        output.mkdir(parents=True, exist_ok=args.resume)
        lock = (output / ".collector.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This batch still has an active collector or agent; wait for it to finish") from exc
        args.lock_fd = lock.fileno()
        if args.resume:
            summary = resume_batch(output, args, prompts)  # Re-read under the lock.
            for run in summary["runs"]:
                if run["status"] == "running":
                    run.update(
                        status="interrupted",
                        failure_reason="collector_interrupted",
                        interruption_recorded_at=datetime.now(UTC).isoformat(),
                    )
                    save(Path(run["run_dir"]) / "manifest.json", run)
        (output / "sessions").mkdir(exist_ok=args.resume)
        completed = refresh_summary(summary)
        summary["status"] = "running"
        summary.pop("runner_error", None)
        save(output / "summary.json", summary)
        selected = dict(zip(args.prompts, prompts))
        for index, label in enumerate(summary["requested_prompts"]):
            if label not in selected:
                continue
            if index in completed:
                print(f"Skipping completed prompt: {label}", flush=True)
                continue
            prompt = selected[label]
            args.prompt_index = index
            print(f"Running {label[:100]} (artifacts: {output})", flush=True)
            try:
                result = collect(prompt, label, args, output)
            except (OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt) as exc:
                summary.update(status="failed", runner_error=type(exc).__name__)
                save(output / "summary.json", summary)
                raise
            summary["runs"].append(result)
            completed = refresh_summary(summary)
            summary["status"] = "running" if result["status"] == "completed" else "failed"
            save(output / "summary.json", summary)
            print(f"{result['status']}: {result['failure_reason'] or result['thread_id']}", flush=True)
            if result["status"] != "completed":
                for error in result["errors"]:
                    if error:
                        detail = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
                        print(f"Agent error: {detail}", file=sys.stderr)
                if result.get("capture_error"):
                    print(f"Session capture error: {result['capture_error']}", file=sys.stderr)
                print(f"Batch stopped. Inspect {result['run_dir']}/manifest.json and stderr.log", file=sys.stderr)
                return 1
        summary["status"] = "completed" if not summary["pending_prompts"] else "incomplete"
        save(output / "summary.json", summary)
        print(f"Collected {len(completed)} verified sessions in {output / 'sessions'}")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
