"""One ACE round on the Graphify configuration: reflect on training sessions, then curate.

    .venv/bin/python cookbook/live_ace.py

Reflection is ``live_reflect``'s: sessions that ran Graphify are handed to a Codex reflector
with the Lesson schema, and each reflection is added to the workspace's journal. Curation then
acts on all of them at once: the markers are refreshed with the new ratings, and a Codex
curator edits a clone of the workspace, one commit per change. Its candidate lands in the
workspace (artifacts/live-reflection/graphify) as branch ``curation/<id>``; the current branch
is left as it was. The run ends with the diff of the configuration the curation made.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cookbook.live_reflect import PROMPT, REPOSITORY, SCHEMA, SOURCES, USEFULNESS, graphify_agent
from thearc.curation import Curator
from thearc.reflection import Reflection, Reflector
from thearc.resources import GitError, agent_at
from thearc.sessions import Sessions
from thearc.sessions.scanner import ShellCommand

SESSIONS = 2  # How many Graphify sessions to reflect on before curating.

CURATION_PROMPT = """Improve the Graphify configuration in response to these reflections on sessions that used it.
Change what the reflections show hurt the work, and protect what they show helped. Prefer tightening or
rewording an instruction to adding new ones; remove an instruction only when the reflections show it
does harm. The sessions came from several harnesses, so do not tailor shared instructions to one
harness. Do not change Graphify's commands or what its hooks run unless a reflection shows it is wrong."""


def run() -> int:
    out = REPOSITORY / "artifacts" / "live-reflection"
    out.mkdir(parents=True, exist_ok=True)
    agent = graphify_agent(out)
    reflector = Reflector("codex")
    reflections: list[Reflection] = []
    with Sessions(out / "sessions.sqlite") as sessions:
        registered = [sessions.add(source, sync=False) for source in SOURCES]
        report = sessions.sync(source_ids=[source.id for source in registered])
        for warning in report.warnings:
            print(f"Index warning: {warning}", file=sys.stderr)

        selected_sessions_ids = sorted({
            match.session_id
            for match in sessions.match(ShellCommand(contains="graphify"))
        })
        print(f"From registered {len(sessions.list())} sessions, selected {len(selected_sessions_ids)}")
        for session_id in selected_sessions_ids[:SESSIONS]:
            session = sessions.get(session_id)
            destination = out / "reflections" / f"{session_id}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            reflection = reflector.handoff(PROMPT, session, agent, SCHEMA)
            reflection.dump_items_to_json(destination)
            recorded = agent.add_reflection(reflection)  # Appends to the workspace's journal.jsonl.
            print(f"{session_id}: {len(reflection)} ratings -> {len(recorded)} journal entries", flush=True)
            reflections.append(reflection)

    print(f"Curating {len(reflections)} reflection(s) with {sum(len(r) for r in reflections)} rating(s)", flush=True)
    curation = Curator("codex").curate(CURATION_PROMPT, reflections, agent, scales=[USEFULNESS])
    print(f"Candidate: branch {curation.branch} ({curation.base[:10]}..{curation.head[:10]})")
    for commit in curation.commits:
        print(f"  {commit.sha[:10]} {commit.message.splitlines()[0]}")
        for changed in commit.changed:
            print(f"      {changed}")
    for entry in curation.entries:
        if entry.kind == "kept":
            print(f"  kept {entry.target}")
    print(f"Summary: {curation.summary}")
    base = agent_at(curation.workspace, curation.base)
    assert base is not None
    diff = base.diff(curation.candidate())  # Clean configurations: content changes only, no markers.
    print(f"\nDiff of the configuration, {curation.base[:10]} -> {curation.branch}:\n")
    print(diff or "(no changes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run()
    except (ValueError, OSError, GitError, subprocess.CalledProcessError) as exc:
        print(f"live_ace: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
