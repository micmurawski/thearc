"""Live check of reflection against a real harness — this makes real (small) model calls.

A tiny agent with a deliberately bad retry rule, and evidence of the agent looping on a
syntax error. ``reflect`` attaches the evidence to the prompt; ``handoff`` installs an
8-event session showing the same thing into the harness and forks it.

    .venv/bin/python thearcv2/live_reflection.py codex reflect
    .venv/bin/python thearcv2/live_reflection.py claude handoff --home /tmp/claude-home
    .venv/bin/python thearcv2/live_reflection.py pi reflect --model <provider/model>

``--home`` installs handed-off sessions into another harness home instead of your own
(Codex then needs ``CODEX_HOME`` set to the same folder, Claude ``CLAUDE_CONFIG_DIR``).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thearcv2.reflection import RUNTIMES, ReflectionError, Reflector
from thearcv2.resources import CategoricalScale, ContextDocument, MetaAgent, ReflectionSchema, Skill
from thearcv2.sessions import Event, Session, SourceReference


class Insight(BaseModel):
    insight: str


SCHEMA = ReflectionSchema(CategoricalScale("usefulness", ["harmful", "neutral", "helpful"]), Insight)
AGENT = MetaAgent(
    name="demo",
    skills={
        "retry": Skill(
            name="retry",
            description="Retry failing commands",
            instructions=(
                "# Retry\n\nWhen a command fails, run it again.\n\n## Limits\n\n"
                "Retry at most 10 times, even for permanent errors like syntax errors.\n"
            ),
        )
    },
    context={
        "AGENTS.md": ContextDocument(
            filename="AGENTS.md", content="# Rules\n\nAlways run the tests before answering.\n"
        )
    },
)
EVIDENCE = """The user asked to run the tests. The agent ran `pytest`, which failed with a SyntaxError in
app.py. Following the retry skill it re-ran pytest 10 times with the same SyntaxError, then told the
user the tests were flaky."""
PROMPT = (
    "Review this agent's configuration against the evidence. Rate the parts of the files that shaped its behaviour."
)


def _event(n: int, kind: str = "message", role: str | None = "assistant", text: str = "", **fields: object) -> Event:
    return Event(
        id=f"live-{n}",
        source_id="live",
        harness="pi",
        session_id="live-session",
        session_native_id="live-session",
        run_id="main",
        cwd=str(Path.cwd()),
        timestamp=f"2026-10-01T10:00:{n:02d}Z",
        kind=kind,
        role=role,
        text=text,
        reference=SourceReference(path=Path("live.jsonl"), generation=0, byte_offset=n, byte_length=1, line=n + 1),
        **fields,  # type: ignore[arg-type]
    )


SESSION = Session.from_events(
    [
        _event(0, role="user", text="Run the tests and tell me if they pass."),
        _event(1, text="Running the test suite."),
        _event(
            2,
            kind="tool_call",
            tool_name="bash",
            call_id="c1",
            arguments={"command": "pytest"},
            text='{"command": "pytest"}',
        ),
        _event(
            3,
            kind="tool_result",
            role="tool",
            tool_name="bash",
            call_id="c1",
            status="failure",
            text="SyntaxError: invalid syntax (app.py, line 3)",
        ),
        _event(
            4,
            kind="tool_call",
            tool_name="bash",
            call_id="c2",
            arguments={"command": "pytest"},
            text='{"command": "pytest"}',
        ),
        _event(
            5,
            kind="tool_result",
            role="tool",
            tool_name="bash",
            call_id="c2",
            status="failure",
            text="SyntaxError: invalid syntax (app.py, line 3)",
        ),
        _event(6, text="I retried 10 times as my retry skill says; the tests seem flaky."),
        _event(7, role="user", text="Thanks."),
    ]
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("harness", choices=sorted(RUNTIMES))
    parser.add_argument("mode", choices=["reflect", "handoff"])
    parser.add_argument("--model")
    parser.add_argument("--home", help="Install handed-off sessions into this harness home.")
    args = parser.parse_args()
    reflector = Reflector(args.harness, model=args.model, home=args.home, timeout=600)
    start = time.time()
    try:
        if args.mode == "reflect":
            reflection = reflector.reflect(PROMPT, AGENT, SCHEMA, evidence=EVIDENCE)
        else:
            reflection = reflector.handoff(PROMPT, SESSION.trajectories[0], AGENT, SCHEMA)
    except ReflectionError as error:
        raise SystemExit(f"{args.harness} {args.mode}: FAILED [{error.code}] {error}") from None
    print(
        f"{args.harness} {args.mode}: {len(reflection)} items after {reflection.attempts} submission(s), "
        f"{time.time() - start:.0f}s, reflector session {reflection.reflector.get('session')}"
    )
    for item in reflection:
        print(f"  {item.rating:<8} {item.ref}  {str(item.metadata.get('insight', ''))[:100]}")
    entries = AGENT.model_copy(deep=True).add_reflection(reflection)  # In memory: this agent has no workspace.
    print(f"  -> {len(entries)} journal entries on {sorted({str(entry.target) for entry in entries})}")


if __name__ == "__main__":
    main()
