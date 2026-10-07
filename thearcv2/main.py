"""Playground for the v2 MetaAgent.

Walks through the whole loop:

1. import an installed configuration into a MetaAgent and journal it as Added;
2. run a (simulated) reflection on it and record the Rated entries;
3. export a workspace *without* the journal — what a reflector gets — and read it back;
4. run a second reflection there: its journal holds only the new ratings;
5. append those ratings to the original journal;
6. export the full workspace (with journal) and four views of the files:
   plain, with ratings, with section IDs, with both;
7. print the agent outline in the same four variants;
8. bump the minor version and install into a Claude project that already has its
   own CLAUDE.md, then import the installation back;
9. commit changes to the workspace in git the way a curator agent would — one
   reason per commit message — and turn those commits into journal entries:
   one entry per changed section, then print each section's change log.

Run from the repository root:

    .venv/bin/python thearcv2/main.py
    .venv/bin/python thearcv2/main.py --harness claude --project ~/some/project --out /tmp/play
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from thearcv2.resources import (
    CategoricalScale,
    ContextDocument,
    Journal,
    MetaAgent,
    ReflectionSchema,
    ResourceTarget,
    Skill,
    agent_at,
    agent_outline,
    annotated_files,
    detect_changes,
    read_workspace,
    record_commits,
    write_workspace,
)

USEFULNESS = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)


class Insight(BaseModel):
    """Operator-defined metadata every reflection item must carry."""

    insight: str
    root_cause_analysis: str = ""


SCHEMA = ReflectionSchema(USEFULNESS, Insight)


def banner(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def show_journal(journal: Journal, *, last: int | None = None) -> None:
    entries = journal.entries[-last:] if last else journal.entries
    print(f"{len(journal)} entries: {dict(Counter(entry.kind for entry in journal.entries))}")
    for entry in entries:
        line = f"  {entry.at:%H:%M:%S} {entry.kind:<8} {entry.target!s:<60} {entry.extra}"
        if entry.kind == "rated":
            line += f"\n{'':12}{entry.value:<8} {entry.ref}  item={entry.item_id[:13]}  \"{entry.metadata['insight']}\""
        else:
            reason = (entry.reason or "-").splitlines()[0]
            line += f"\n{'':12}commit {(entry.commit or '-')[:8]}  reason: {reason}"
            if entry.kind == "moved":
                line += f"\n{'':12}was {entry.previous}"
        print(line)


def simulate_reflection(agent: MetaAgent, reflector: str, count: int, rng: random.Random) -> list[dict]:
    """What a reflector would return: references to lines it judged, a rating, and metadata.

    Some ranges spill past their section on purpose, so they resolve to two
    targets. The last item points at a file that does not exist and is rejected.
    """
    files = agent.files()
    spans = [span for span in files.spans if span.target.section is not None] or list(files.spans)
    items = []
    for _ in range(count):
        span = rng.choice(spans)
        total = len(files.files[span.path].splitlines())
        start = rng.randint(span.start, max(span.start, span.end))
        end = min(total, max(start, span.end) + rng.choice([0, 0, 0, 3]))
        rating = rng.choice(USEFULNESS.labels)
        items.append({
            "ref": f"{span.path}#L{start}" + (f"-L{end}" if end != start else ""),
            "rating": rating,
            "insight": f"{reflector} found {span.target.section or span.target.name!r} {rating} in a session.",
            "root_cause_analysis": "simulated",
        })
    items.append({"ref": "skills/missing/SKILL.md#L1", "rating": "helpful", "insight": "points nowhere"})
    return items


def reflect(agent: MetaAgent, reflector: str, count: int, rng: random.Random) -> None:
    items = simulate_reflection(agent, reflector, count, rng)
    result = SCHEMA.record(items, agent, reflector=reflector, session=f"{reflector}-session-1")
    print(f"{len(items)} items -> {len(result.entries)} Rated entries, {len(result.rejected)} rejected")
    for rejection in result.rejected:
        print(f"  rejected item {rejection.index}: {rejection.reason}")
    agent.journal.record(*result.entries)


def write_files(files: dict[str, str], root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def bump_minor(version: str | None) -> str:
    """``1.4.2`` -> ``1.5.0``; no version yet -> ``0.1.0``."""
    if version is None:
        return "0.1.0"
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    return f"{parts[0]}.{int(parts[1]) + 1}.0"


PROJECT_CLAUDE_MD = """# My project

Use uv for everything.

## graphify

An outdated graphify section that the install will replace.

## Testing

Run pytest before committing.
"""


def show_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(root)}")


def sample_agent() -> MetaAgent:
    return MetaAgent(name="sample", skills=[Skill(
        name="retry", description="Retry flaky operations",
        instructions="# Retry\n\nUse retries.\n\n## Examples\n\nretry(3)\n\n## Limits\n\nNever more than 3.\n")],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nBe brief.\n\n## Tests\n\nRun pytest.\n")])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harness", default="antigravity", choices=["claude", "codex", "antigravity", "pi"])
    parser.add_argument("--project", type=Path, default=REPOSITORY / "tests" / "fastapi")
    parser.add_argument("--out", type=Path, default=REPOSITORY / "thearcv2" / ".playground")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    out = args.out.resolve()
    # Each run starts from scratch.
    for generated in ("workspace", "workspace-for-reflection", "views", "claude-project",
                      "agent-repo"):
        shutil.rmtree(out / generated, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    banner(f"1. Import the {args.harness} configuration from {args.project}")
    agent = MetaAgent.from_installation(args.harness, args.project, name="playground")
    if not agent.files().files:
        print("Nothing installed there; using a small sample agent instead.")
        agent = sample_agent()
    print(f"skills={list(agent.skills)} context={list(agent.context)} hooks={list(agent.hooks)} "
          f"mcps={list(agent.mcps)} resources={list(agent.resources)}")
    print(f"revision {agent.revision}, {len(agent.files().targets)} targets")
    agent.journal.record(*detect_changes(None, agent, reason=f"Imported from {args.harness}", by="import"))
    show_journal(agent.journal, last=5)

    banner("2. Reflection #1 on the agent")
    reflect(agent, "reflector-a", count=5, rng=rng)
    show_journal(agent.journal, last=6)

    banner("3. Export a workspace WITHOUT the journal (what a reflector sees) and read it back")
    blind = write_workspace(agent, out / "workspace-for-reflection", journal=False, overwrite=True)
    print(f"{blind}: journal.jsonl exists? {(blind / 'journal.jsonl').exists()}")
    reflection_copy = read_workspace(blind)
    print(f"read back: same revision? {reflection_copy.revision == agent.revision}; "
          f"journal entries: {len(reflection_copy.journal)}")

    banner("4. Reflection #2 on the journal-free copy: its journal holds only the new ratings")
    reflect(reflection_copy, "reflector-b", count=4, rng=rng)
    show_journal(reflection_copy.journal)

    banner("5. Append reflection #2 to the original journal")
    agent.journal.record(*reflection_copy.journal.entries)
    show_journal(agent.journal, last=len(reflection_copy.journal))
    for target, summary in sorted(agent.journal.summaries(USEFULNESS).items(), key=lambda item: str(item[0])):
        print(f"  {target!s:<60} {USEFULNESS.render(summary)}")

    banner("6. Export the full workspace and the four file views")
    print(write_workspace(agent, out / "workspace", overwrite=True))
    variants = {"plain": {}, "ratings": {"scales": USEFULNESS}, "ids": {"ids": True},
                "ids+ratings": {"scales": USEFULNESS, "ids": True}}
    for variant, options in variants.items():
        write_files(annotated_files(agent, **options), out / "views" / variant)
        print(out / "views" / variant)

    for variant, options in variants.items():
        banner(f"7. Outline: {variant}")
        print(agent_outline(agent, **options))

    install_into_claude(agent, out / "claude-project")
    commit_changes(agent, out / "agent-repo", rng)


def install_into_claude(agent: MetaAgent, project: Path) -> None:
    banner("8. Bump the minor version and install into a Claude project")
    release = agent.model_copy(update={"version": bump_minor(agent.version)})
    print(f"version {agent.version} -> {release.version}; revision unchanged: {release.revision == agent.revision}")
    project.mkdir(parents=True)
    (project / "CLAUDE.md").write_text(PROJECT_CLAUDE_MD, encoding="utf-8")
    print(f"{project} starts with the project's own CLAUDE.md:\n{PROJECT_CLAUDE_MD}")

    installation = release.install("claude", project)
    print(f"installed {len(installation.files)} files, context {installation.context}, hooks {installation.hooks}, "
          f"MCP servers {installation.mcps}, skipped {installation.skipped}")
    show_tree(project)
    print(f"\n{installation.marker}:\n{(project / installation.marker).read_text(encoding='utf-8')}")
    print(f"CLAUDE.md after install (graphify replaced, the rest kept):\n{(project / 'CLAUDE.md').read_text()}")

    imported = MetaAgent.from_installation("claude", project)
    print(f"imported back: {imported.name} {imported.version}, same configuration: "
          f"{imported.revision == release.revision}")


GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "curator-agent", "GIT_AUTHOR_EMAIL": "curator@example.test",
           "GIT_COMMITTER_NAME": "curator-agent", "GIT_COMMITTER_EMAIL": "curator@example.test"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    sha = git(repo, "rev-parse", "HEAD")
    print(f"  commit {sha[:8]}  {message.splitlines()[0]}")
    return sha


def change_lines(repo: Path, target: ResourceTarget, change) -> None:
    """Apply ``change(lines, span)`` to the file holding ``target`` at the repository's current state."""
    files = agent_at(repo, "HEAD").files()
    span = files.span(target)
    path = repo / span.path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    change(lines, span)
    path.write_text("".join(lines), encoding="utf-8")


def add_note(repo: Path, target: ResourceTarget, note: str) -> None:
    change_lines(repo, target, lambda lines, span: lines.insert(span.start, f"\n{note}\n"))


def rename(repo: Path, target: ResourceTarget, title: str) -> None:
    def retitle(lines, span):
        hashes = lines[span.start - 1].split(" ", 1)[0]
        lines[span.start - 1] = f"{hashes} {title}\n"
    change_lines(repo, target, retitle)


def commit_changes(agent: MetaAgent, repo: Path, rng: random.Random) -> None:
    banner("9. Changes as commits: the commit message is the reason, each changed section gets its own entry")
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write_workspace(agent, repo, overwrite=True)
    base = commit(repo, "Import configuration")

    files = agent.files()
    texts = Counter(files.owned_text(t) for t in files.targets)
    nested = {(s.path, s.start) for s in files.spans for o in files.spans
              if o.path == s.path and o.depth > s.depth and s.start < o.start <= s.end}
    # Sections with their own, unique text; leaves can be renamed without moving children along.
    candidates = sorted((s for s in files.spans if s.target.section and files.owned_text(s.target)
                         and texts[files.owned_text(s.target)] == 1), key=lambda s: (s.path, s.start))
    leaves = [s for s in candidates if (s.path, s.start) not in nested]
    renamed = rng.choice(leaves).target
    first, second, third = rng.sample([s.target for s in candidates if s.target != renamed], 3)
    title = lambda target: target.section.rsplit(" > ", 1)[-1]

    add_note(repo, first, "Prefer the narrowest command that answers the question.")
    commit(repo, f"Clarify '{title(first)}'\n\nReflection rated it harmful: the agent ran broad commands first.")
    document = next(iter(sorted(agent.context)), None)
    path = repo / ("context/" + document if document else next(p for p in files.files if p.endswith("SKILL.md")))
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n\n## Troubleshooting\n\n"
                    "If an expected file is missing, say so instead of guessing.\n", encoding="utf-8")
    commit(repo, "Add troubleshooting guidance after sessions that guessed missing files")
    add_note(repo, second, "Check the result before moving on.")
    add_note(repo, third, "Check the result before moving on.")
    commit(repo, "Require a check after two steps that reflections rated harmful")
    rename(repo, renamed, f"{title(renamed)} (revised)")
    commit(repo, f"Rename '{title(renamed)}' to match the CLI")

    banner("9a. What each commit changed: agent_at(repo, f'{sha}^').diff(agent_at(repo, sha))")
    for line in git(repo, "log", "--reverse", "--format=%H %s", f"{base}..HEAD").splitlines():
        sha, subject = line.split(" ", 1)
        print(f"\n### {sha[:8]} {subject}")
        print(agent_at(repo, f"{sha}^").diff(agent_at(repo, sha), context_lines=1))

    banner("9b. record_commits: one journal entry per changed section, the commit message as its reason")
    entries = record_commits(repo, agent.journal, since=base)
    show_journal(agent.journal, last=len(entries))

    banner("9c. The same change entries as stored in journal.jsonl")
    agent = agent_at(repo, "HEAD").model_copy(update={"journal": agent.journal})
    write_workspace(agent, repo, overwrite=True)
    stored = [json.loads(line) for line in (repo / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    for kind in ("modified", "added", "moved"):
        record = next(record for record in stored if record["kind"] == kind and record["commit"])
        print(json.dumps(record, indent=2, ensure_ascii=False))
    commit(repo, "Record journal")
    print(f"recording again adds {len(record_commits(repo, agent.journal))} entries")

    banner("9d. Change log per section, following renames: journal.changes(target)")
    for target in dict.fromkeys(entry.target for entry in entries):
        print(f"\n{target}  [{target.id}]")
        for change in agent.journal.changes(target):
            reason, sha = (change.reason or "-").splitlines()[0], (change.commit or "-")[:8]
            print(f"  {change.at.astimezone():%Y-%m-%d %H:%M} {sha:<8} {change.kind:<8} {reason}")
            if change.kind == "modified":
                print("\n".join(f"{'':4}{line}" for line in change.diff(context_lines=1).splitlines()))
            elif change.kind == "moved":
                print(f"{'':4}from {change.previous}")

    banner(f"9d. journal.diff(commit={base[:8]!r}, head='HEAD'): net change per section, journal only")
    print(agent.journal.diff(commit=base[:8], head="HEAD", context_lines=1))


if __name__ == "__main__":
    main()
