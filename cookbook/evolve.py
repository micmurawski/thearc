"""Evolve Graphify over epochs with thearc: sessions on every harness → reflect → curate → merge → release.

    .venv/bin/python cookbook/evolve.py seed              # epoch 0: Graphify as the harnesses ran it
    .venv/bin/python cookbook/evolve.py epoch 1 [--push]  # then 2, 3, 4; a stopped epoch resumes when run again

Each epoch installs the current release into the FastAPI project for Claude, Codex, Pi and OpenCode, runs coding
tasks with each, reflects on every session, curates one candidate per harness, merges the candidates and releases
the result, with a report after each step. Epoch 1 uses the 10 prompts in prompts/; later epochs add 5 new prompts
written by Codex, and rerun 3 probe prompts so versions can be compared on the same tasks. Everything lands in
artifacts/evolution/: the workspace (graphify/, github.com/micmurawski/graphify), the reflections, and
epochs/<n>/ with the prompts, sessions, candidates and reports.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cookbook.live_ace import CURATION_PROMPT
from cookbook.live_ace_with_merge import MERGE_PROMPT
from cookbook.live_reflect import PROMPT, SCHEMA, USEFULNESS
from thearc.curation import Curator, Merger
from thearc.harnesses import Harness
from thearc.reflection import Reflection, Reflector
from thearc.resources import MetaAgent
from thearc.resources.report import write_report
from thearc.sessions import Sessions

ROOT = Path(__file__).resolve().parents[1]
PROJECT, BASE = ROOT / "tests" / "fastapi", "75e0095ea"  # FastAPI with Graphify 0.9.68, installed by Graphify.
OUT = ROOT / "artifacts" / "evolution"
WORKSPACE, INSTALLED = OUT / "graphify", OUT / "project"
MODEL = "minimax/MiniMax-M3#thinking"
HARNESSES: dict[Harness, list[str]] = {"claude": [], "codex": [], "pi": ["-m", MODEL], "opencode": ["-m", MODEL]}
# Sessions run Graphify's CLI, which is installed in thearc's virtual environment: put it on their PATH.
SESSION_ENV = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
PROBES = ["02_deprecated_at_route_field.md", "06_structured_validation_error_code.md", "10_route_class_map.md"]
WRITE_PROMPTS = """Write 5 new coding tasks for AI agents working on the FastAPI repository in this directory:
realistic features or fixes in fastapi/ that need reading several files, finishable in one session, each ending
with a pytest command its new tests must pass. Describe only the task: never mention Graphify, knowledge
graphs or which tools to use. Do not repeat these tasks:
{existing}

Use exactly this format (keep the headings and the ```text block):

{example}

Reply with only a JSON array of 5 objects {{"name": "NN_short_snake_name.md", "markdown": "..."}}, numbered from
{first}."""


def seed() -> None:
    """Epoch 0: Graphify as the harnesses ran it: the skill and context they loaded, each harness's own hooks."""
    snapshot = OUT / "base"
    git(PROJECT, "worktree", "add", "--detach", snapshot, BASE)
    installed = {h: MetaAgent.from_installation(h, snapshot, name="graphify")
                 for h in ("antigravity", "claude", "codex")}
    git(PROJECT, "worktree", "remove", "--force", snapshot)
    shared = installed["antigravity"]  # .agents/skills and AGENTS.md / CLAUDE.md: what Claude, Codex and Pi loaded
    agent = MetaAgent(
        name="graphify",
        version=shared.skills["graphify"].files[".graphify_version"].strip(),
        skills=shared.skills,
        context=shared.context,
        hooks={**installed["claude"].hooks, **installed["codex"].hooks},
        owns_hooks=["graphify *"],
    )
    agent.init_workspace(WORKSPACE, branch="main", reason=f"Import Graphify {agent.version} as installed")
    report(OUT / "epochs" / "0" / "report.html")


def epoch(n: int, push: bool) -> None:
    out = OUT / "epochs" / str(n)
    agent = MetaAgent.from_workspace(WORKSPACE)  # main: the current release
    if not shutil.which("graphify", path=SESSION_ENV["PATH"]):  # Else sessions fail on a missing CLI, not on
        raise SystemExit("sessions need Graphify's CLI on PATH")  # the instructions being evolved.

    # 1. Install the release for every harness. The commit is where every session starts.
    if not INSTALLED.exists():
        git(PROJECT, "worktree", "add", "-B", "evolution", INSTALLED, BASE)
    for harness in HARNESSES:
        agent.install(harness, INSTALLED, force=True)
    git(INSTALLED, "add", "-A")
    git(INSTALLED, "commit", "-m", f"Install {agent.name} {agent.version}", check=False)  # No change, no commit.
    base = git(INSTALLED, "rev-parse", "HEAD")

    # 2. Run every prompt with every harness, in parallel; keep the sessions that ran this release.
    prompts = write_prompts(n, out / "prompts")
    with ThreadPoolExecutor() as pool:
        evidence = dict(zip(HARNESSES, pool.map(lambda h: run_sessions(h, prompts, base, out, agent), HARNESSES)))
    with Sessions(OUT / "sessions.sqlite") as sessions:  # Index them, so reflectors can hand them off.
        for harness in HARNESSES:
            sessions.add(out / "sessions" / harness, source_id=f"epoch-{n}-{harness}")

    # 3. One pipeline per harness, in parallel: reflect on its sessions, then curate its own candidate.
    pipelines = [harness for harness in HARNESSES if evidence[harness]]
    with ThreadPoolExecutor() as pool:
        candidates = list(pool.map(lambda h: curate(agent, h, evidence[h], out), pipelines))
    report(out / "report-curated.html")

    # 4. Merge the candidates section by section (a single candidate is the merge).
    if len(candidates) == 1:
        merged = candidates[0]
    elif (out / "merged").exists():
        merged = MetaAgent.from_workspace(out / "merged")
    else:
        merged = Merger("codex").merge(MERGE_PROMPT, candidates, into=out / "merged", epoch=n).agent
    report(out / "report-merged.html")

    # 5. Release it and move main to the release, so the next epoch builds on it.
    released = merged.release(part="patch", include_scales=True)
    git(WORKSPACE, "merge", "--ff-only", f"release/{released.version}")
    if push:
        branches = [f"refs/heads/{kind}/*" for kind in ("candidate", "merge", "release")]
        git(WORKSPACE, "push", "origin", "main", *branches)
    report(out / "report.html")
    print(f"Epoch {n}: {agent.version} → {released.version}; reports in {out}")


def write_prompts(n: int, folder: Path) -> list[Path]:
    """Epoch 1: the 10 prompts in prompts/. Later epochs: the probes and 5 new prompts written by Codex."""
    if not folder.exists():
        folder.mkdir(parents=True)
        for name in ([p.name for p in (ROOT / "prompts").glob("[0-9][0-9]_*.md")] if n == 1 else PROBES):
            shutil.copy(ROOT / "prompts" / name, folder / name)
        if n > 1:
            existing = "\n".join(p.read_text().splitlines()[0] for p in (OUT / "epochs").glob("*/prompts/*.md"))
            request = WRITE_PROMPTS.format(existing=existing, first=10 + 5 * (n - 2) + 1,
                                           example=(ROOT / "prompts" / "01_add_request_id_header.md").read_text())
            answer = subprocess.run(["codex", "exec", "--sandbox", "read-only", "-C", str(PROJECT), "-"],
                                    input=request, capture_output=True, text=True, check=True).stdout
            for task in json.loads(answer[answer.index("["): answer.rindex("]") + 1]):
                (folder / task["name"]).write_text(task["markdown"])
    return sorted(folder.glob("*.md"))


def run_sessions(harness: Harness, prompts: list[Path], base: str, out: Path, agent: MetaAgent) -> list[str]:
    """Run the prompts with one harness; return the sessions that ran this release (its version stamp is in their
    transcript; OpenCode records no context, so its completed sessions count)."""
    batch = out / "sessions" / harness
    resume = ["--resume"] if batch.exists() else []  # The collector skips the prompts it has finished.
    subprocess.run([sys.executable, ROOT / "scripts" / "run_agent_prompts.py", "--harness", harness, "-C", PROJECT,
                    "--base", base, "--output", batch, *HARNESSES[harness], *resume, *prompts],
                   env=SESSION_ENV, check=False)
    stamp = f"{agent.name} {agent.version}"
    runs = [json.loads(manifest.read_text()) for manifest in batch.glob("runs/*/manifest.json")]
    return [run["thread_id"] for run in runs if run["status"] == "completed"
            and (harness == "opencode" or stamp in Path(run["transcript"]).read_text())]


def curate(agent: MetaAgent, harness: Harness, session_ids: list[str], out: Path) -> MetaAgent:
    """Reflect on each session against this harness's candidate, then curate the candidate."""
    folder = out / "candidates" / harness
    if folder.exists():
        candidate = MetaAgent.from_workspace(folder)
        if "Record curation" in git(folder, "log", "-1", "--format=%s"):
            return candidate  # Curated by an earlier, stopped run.
    else:
        candidate = agent.to_worktree(folder, branch=f"candidate/epoch-{out.name}/{harness}")
    reflections = []
    (OUT / "reflections").mkdir(exist_ok=True)
    with Sessions(OUT / "sessions.sqlite") as sessions:
        for session_id in session_ids:
            saved = OUT / "reflections" / f"{session_id}.json"
            if saved.exists():
                reflection = Reflection.load(saved, SCHEMA)
            else:
                installed = f"The session ran where thearc had installed {agent.name} {agent.version} for {harness}: " \
                            "the files you are given, loaded by the harness when the session started."
                reflection = Reflector("codex").handoff(PROMPT, sessions.get(session_id), candidate, SCHEMA,
                                                        provenance=installed)
                reflection.dump_items_to_json(saved)
            candidate.add_reflection(reflection)
            reflections.append(reflection)
    Curator("codex").curate(CURATION_PROMPT, reflections, candidate, scales=[USEFULNESS], pipeline=harness)
    return MetaAgent.from_workspace(folder)


def report(path: Path) -> None:
    write_report(WORKSPACE, path, scales=[USEFULNESS], reflections=OUT / "reflections")


def git(repo: Path, *args: object, check: bool = True) -> str:
    command = ["git", "-C", repo, "-c", "user.name=thearc", "-c", "user.email=thearc@localhost", *args]
    return subprocess.run(list(map(str, command)), capture_output=True, text=True, check=check).stdout.strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["seed", "epoch"])
    parser.add_argument("number", type=int, nargs="?", default=1)
    parser.add_argument("--push", action="store_true", help="push main, candidates, merges and releases")
    args = parser.parse_args()
    if args.command == "seed":
        seed()
    else:
        epoch(args.number, args.push)
