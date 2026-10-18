"""ACE with parallel pipelines on the Graphify configuration: reflect and curate per pipeline, then merge.

    .venv/bin/python cookbook/live_ace_with_merge.py

The Graphify workspace (artifacts/live-reflection/graphify) is the shared base. Each pipeline gets
its own worktree of it, branched from the same commit, and works on its own share of the sessions
that ran Graphify: a Codex reflector reflects on each session with the Lesson schema, and a Codex
curator acts on that pipeline's reflections, moving the worktree forward to its candidate. A Codex
merger then combines the candidates section by section into a merged candidate, checked out in a
worktree of its own. Every run gets its own directory under artifacts/live-reflection/runs/ and its
own branch names, so runs never collide. The run prints the diff from the base to the merged
configuration, then releases it: the version gets a patch bump (0.9.68 -> 0.9.69) and a new branch
release/<version> appears in the workspace repository, with RATINGS.md listing every section's ratings.
With --push [REMOTE] (default origin) the release branch is pushed too.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cookbook.live_ace import CURATION_PROMPT
from cookbook.live_reflect import PROMPT, REPOSITORY, SCHEMA, SOURCES, USEFULNESS, graphify_agent
from thearc.curation import Curator, Merger
from thearc.reflection import Reflection, Reflector
from thearc.resources import GitError, MetaAgent, agent_at
from thearc.sessions import Sessions
from thearc.sessions.scanner import ShellCommand

PIPELINES = 2  # Parallel pipelines, each its own worktree and candidate.
SESSIONS_PER_PIPELINE = 3  # Sessions each pipeline reflects on before curating.

MERGE_PROMPT = """Merge the candidates into the best Graphify configuration. Each pipeline reflected on different
sessions, so a change one candidate makes and another does not is usually new evidence, not a disagreement.
Where candidates change the same section, keep what each one's reasons support and resolve real
contradictions in favour of the better-evidenced reason. Keep shared instructions harness-neutral."""


def pipeline(name: str, candidate: MetaAgent, session_ids: list[str], sessions: Sessions, out: Path) -> None:
    """Reflect on each session against ``candidate``, then curate it: the worktree ends at its candidate."""
    reflector = Reflector("codex")
    reflections: list[Reflection] = []
    for session_id in session_ids:
        reflection = reflector.handoff(PROMPT, sessions.get(session_id), candidate, SCHEMA, pipeline=name)
        reflection.dump_items_to_json(out / "reflections" / f"{session_id}.json")
        recorded = candidate.add_reflection(reflection)  # Into this candidate's journal only.
        print(f"[{name}] {session_id}: {len(reflection)} ratings -> {len(recorded)} journal entries", flush=True)
        reflections.append(reflection)
    curation = Curator("codex").curate(CURATION_PROMPT, reflections, candidate, scales=[USEFULNESS], pipeline=name)
    print(f"[{name}] curated: {len(curation.commits)} commit(s); {curation.summary}", flush=True)
    for commit in curation.commits:
        print(f"[{name}]   {commit.sha[:10]} {commit.message.splitlines()[0]}")


def run(push: str | None = None) -> int:
    out = REPOSITORY / "artifacts" / "live-reflection"
    out.mkdir(parents=True, exist_ok=True)
    agent = graphify_agent(out)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = out / "runs" / stamp
    (run_dir / "reflections").mkdir(parents=True)

    with Sessions(out / "sessions.sqlite") as sessions:
        registered = [sessions.add(source, sync=False) for source in SOURCES]
        report = sessions.sync(source_ids=[source.id for source in registered])
        for warning in report.warnings:
            print(f"Index warning: {warning}", file=sys.stderr)
        selected = sorted({match.session_id for match in sessions.match(ShellCommand(contains="graphify"))})
        needed = PIPELINES * SESSIONS_PER_PIPELINE
        print(f"From registered {len(sessions.list())} sessions, selected {len(selected)}; using {needed}")
        if len(selected) < needed:
            raise ValueError(f"need {needed} sessions that ran Graphify, found {len(selected)}")

        candidates = []
        for index in range(PIPELINES):
            name = f"p{index + 1}"
            candidate = agent.to_worktree(run_dir / name, branch=f"candidate/{stamp}/{name}")
            share = selected[index * SESSIONS_PER_PIPELINE:(index + 1) * SESSIONS_PER_PIPELINE]
            pipeline(name, candidate, share, sessions, run_dir)
            candidates.append(candidate)

    merge = Merger("codex").merge(MERGE_PROMPT, candidates, into=run_dir / "merged", run=stamp)
    print(f"\nMerged {len(merge.candidates)} candidates from {merge.base[:10]} into {merge.branch} "
          f"({len(merge.proposals)} section(s) proposed, {len(merge.commits)} commit(s)):")
    for commit in merge.commits:
        print(f"  {commit.sha[:10]} {commit.message.splitlines()[0]}")
        for changed in commit.changed:
            print(f"      {changed}")
    print(f"Summary: {merge.summary}")
    base = agent_at(agent.checked_workspace("diff"), merge.base)
    assert base is not None
    diff = base.diff(merge.agent)  # Clean configurations: content changes only, no markers.
    print(f"\nDiff of the configuration, {merge.base[:10]} -> {merge.branch}:\n")
    print(diff or "(no changes)")
    print(f"Merged worktree: {merge.agent.workspace}")

    # Versioning: the merged candidate becomes the next version, on a branch of its own.
    released = merge.agent.release(part="patch", include_scales=True, push=push)
    print(f"\nReleased {released.name} {merge.agent.version} -> {released.version} "
          f"on branch release/{released.version} ({released.revision})")
    if push:
        print(f"Pushed release/{released.version} to {push}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", nargs="?", const="origin", metavar="REMOTE",
                        help="Push the release branch to REMOTE (default origin) after releasing.")
    args = parser.parse_args(argv)
    try:
        return run(push=args.push)
    except (ValueError, OSError, GitError, subprocess.CalledProcessError) as exc:
        print(f"live_ace_with_merge: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
