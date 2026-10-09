"""Merging: a merger combines candidates curated in parallel from the same base into one.

Each candidate is an agent on its own worktree of the same workspace (``agent.to_worktree``),
moved forward by its pipeline (reflections, then curation). The merger works out their shared
base ``B`` from git, gathers the **proposals** — every section any candidate changed, grouped by
lineage ID, with each candidate's text and reasons — and decides each section's final shape in a
clone at ``B``, one commit per section. The merged journal holds the base journal, every
candidate's reflection entries, and the merger's own changes; the result lands on branch
``merge/<id>``, checked out in a new worktree.
"""

from __future__ import annotations

import re
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.reflection.mcp import serve
from thearc.reflection.runtimes import RUNTIMES, Runtime
from thearc.resources.agent import MetaAgent
from thearc.resources.files import AgentFiles
from thearc.resources.git import _git, agent_at, commit, commit_journal, record_commits
from thearc.resources.journal import Change, Entry, Journal, Rated, load_journal
from thearc.resources.targets import ResourceTarget
from thearc.resources.workspace import JOURNAL_FILE

from .curator import WORKSPACE, Commit, CurationError, CurationTools

INSTRUCTIONS = """You are merging several improved versions — candidates — of an AI agent's configuration. Each
was produced independently from the same base by a curator acting on reviews of how the agent behaved.
For every section below, read each candidate's version and the reasons behind it, and decide the
section's final shape: take one version as it is, combine them, rewrite, or keep the base. Judge by the
reasons and the ratings, not by how many candidates agree. Keep the configuration consistent as a whole:
no contradictions between sections and no duplicated guidance."""

COMMITTING = """Commit each section's final shape with `commit_change(message)`: **one section per commit**,
and **the reason as the message** — what you took, combined, or left out and why, naming the candidates
and commits it draws on (e.g. `candidate/1 1a2b3c4`). A section you leave as it is at the base needs no
commit. When every section is decided, call `finish(summary)`."""


@dataclass(frozen=True)
class Version:
    """One candidate's version of a section: its address there (None if it removed it), text, and why."""

    candidate: str
    target: ResourceTarget | None
    text: str | None
    changes: tuple[Change, ...]
    ratings: tuple[Rated, ...]


@dataclass(frozen=True)
class Proposal:
    """A section some candidate changed: its lineage ID, address and text at the base, and every version."""

    lineage: str
    target: ResourceTarget | None
    text: str | None
    versions: tuple[Version, ...]


@dataclass(frozen=True)
class Merge:
    """One merger run: the merged candidate's branch and worktree, its commits, and the journal entries added."""

    id: str
    base: str
    branch: str
    head: str
    commits: tuple[Commit, ...]
    entries: tuple[Entry, ...]
    summary: str
    merger: dict[str, Any]
    candidates: dict[str, str]  # Branch → head commit merged.
    proposals: tuple[Proposal, ...]
    agent: MetaAgent
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Merger:
    """A merger: a harness and a model.

        merger = Merger("codex")
        merge = merger.merge(PROMPT, [candidate_1, candidate_2])
        merge.agent, merge.branch, merge.commits
    """

    def __init__(self, harness: str = "codex", *, model: str | None = None, effort: str | None = None,
                 timeout: float = 1800, runtime: Runtime | None = None):
        if runtime is None:
            if harness not in RUNTIMES or harness == "antigravity":
                raise ValueError(f"no merger runtime for {harness!r}; available: codex, claude, pi")
            runtime = RUNTIMES[harness](model=model, effort=effort, timeout=timeout)
        self.runtime = runtime

    @property
    def harness(self) -> str:
        return self.runtime.harness

    def merge(self, prompt: str, candidates: Sequence[MetaAgent], *, into: str | Path | None = None,
              **extra: Any) -> Merge:
        """Merge candidates — agents on worktrees of one workspace, branched from the same commit.

        Each candidate is read as committed on its worktree (pending journal lines are committed
        first). The merged candidate is checked out at ``into`` (default: next to the first
        candidate). ``extra`` is stored on every entry the merge records.
        """
        roots = [_root_of(candidate) for candidate in candidates]
        if len(roots) < 2:
            raise ValueError("merging needs at least two candidates")
        if len({_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip() for root in roots}) != 1:
            raise ValueError("candidates must be worktrees of one workspace")
        for root in roots:
            commit_journal(root)
            if _git(root, "status", "--porcelain").strip():
                raise ValueError(f"commit or undo pending edits in {root} first")
        heads = [_git(root, "rev-parse", "HEAD").decode().strip() for root in roots]
        names = [_git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip() for root in roots]
        base = _git(roots[0], "merge-base", heads[0], heads[1]).decode().strip()
        for index in range(len(heads)):
            for other in range(index + 1, len(heads)):
                if _git(roots[0], "merge-base", heads[index], heads[other]).decode().strip() != base:
                    raise ValueError("candidates do not share one base; branch them from the same commit")
        proposals, ratings = _proposals(roots[0], base, list(zip(names, roots, heads, strict=True)))

        identifier = "merge_" + uuid.uuid4().hex
        branch = f"merge/{identifier.removeprefix('merge_')[:12]}"
        stamp = {"merge": identifier, "merger": self.harness,
                 **({"model": self.runtime.model} if self.runtime.model else {}), **extra}
        with tempfile.TemporaryDirectory(prefix="thearc-merge-") as directory:
            clone = Path(directory).resolve() / "agent"
            _git(Path(directory), "clone", "-q", "--no-checkout", str(roots[0]), str(clone))
            _git(clone, "checkout", "-q", "-b", branch, base)
            tools = CurationTools(clone, f"merger ({self.harness})")
            with serve(tools.specs()) as endpoint, self.runtime.conversation(clone, endpoint, edit=True) as talk:
                talk.send(_message(prompt, proposals, clone))
            if tools.summary is None:
                raise CurationError("no_finish", f"The merger ended without calling finish "
                                    f"({len(tools.commits)} commit(s) made)")
            journal = Journal.load(clone / JOURNAL_FILE)
            recorded: list[Entry] = list(record_commits(clone, journal, since=base, **stamp))
            recorded += journal.merge(*ratings)  # What every pipeline learned.
            if recorded:
                Journal.append(clone / JOURNAL_FILE, recorded)
                _git(clone, "add", JOURNAL_FILE)
                commit(clone, f"Record {identifier}")
            head = _git(clone, "rev-parse", "HEAD").decode().strip()
            _git(roots[0], "fetch", "-q", str(clone), f"{branch}:{branch}")
        target = Path(into) if into is not None else roots[0].parent / branch.replace("/", "-")
        _git(roots[0], "worktree", "add", "-q", str(target.resolve()), branch)
        merger = {"harness": self.harness, "model": self.runtime.model, "session": talk.id}
        return Merge(identifier, base, branch, head, tuple(tools.commits), tuple(recorded), tools.summary, merger,
                     dict(zip(names, heads, strict=True)), tuple(proposals),
                     MetaAgent.from_workspace(target.resolve()))


def _root_of(candidate: MetaAgent) -> Path:
    if candidate.workspace is None:
        raise ValueError("candidates must be agents on workspaces (see agent.to_worktree)")
    return candidate.workspace


def _owned(files: AgentFiles, target: ResourceTarget | None) -> str | None:
    if target is None:
        return None
    try:
        return files.owned_text(target)
    except KeyError:
        return None


def _proposals(repo: Path, base: str, candidates: list[tuple[str, Path, str]]) -> tuple[list[Proposal], list[Rated]]:
    """Every section a candidate changed since ``base``, by lineage ID; and every candidate's new ratings."""
    base_agent = agent_at(repo, base)
    if base_agent is None:
        raise ValueError(f"{base} holds no workspace")
    base_journal = Journal(entries=load_journal(_git(repo, "show", f"{base}:{JOURNAL_FILE}").decode()))
    known = {entry.id for entry in base_journal.entries}
    at_base = {identifier: target for target, identifier in base_journal.ids().items()}
    base_files = base_agent.files()
    versions: dict[str, list[Version]] = {}
    ratings: dict[str, Rated] = {}
    for name, root, head in candidates:
        journal = Journal.load(root / JOURNAL_FILE)
        commits = set(_git(root, "rev-list", f"{base}..{head}").decode().split())
        files = AgentFiles.from_agent(MetaAgent.from_workspace(root, journal=False))
        lineage = {entry.id: line for line in journal.lineages() for entry in line.entries}
        changes: dict[str, list[Change]] = {}
        for change in journal.changes():
            if change.commit in commits:
                changes.setdefault(lineage[change.id].id, []).append(change)
        new_ratings = [rating for rating in journal.ratings() if rating.id not in known]
        ratings |= {rating.id: rating for rating in new_ratings}
        by_id = {line.id: line for line in journal.lineages()}  # A section re-added after removal: the latest.
        for identifier, made in changes.items():
            line = by_id[identifier]
            current = None if line.removed else line.target
            rated = tuple(rating for rating in new_ratings if lineage[rating.id].id == identifier)
            versions.setdefault(identifier, []).append(
                Version(name, current, _owned(files, current), tuple(made), rated))
    proposals = [Proposal(identifier, at_base.get(identifier), _owned(base_files, at_base.get(identifier)),
                          tuple(found)) for identifier, found in versions.items()]
    return sorted(proposals, key=lambda p: str(p.target or p.versions[0].target)), list(ratings.values())


def _fenced(text: str | None) -> str:
    if text is None:
        return "*(removed)*"
    if not text.strip():
        return "*(empty)*"
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}text\n{text}\n{fence}"


def _message(prompt: str, proposals: list[Proposal], workspace: Path) -> str:
    blocks = []
    for proposal in proposals:
        address = proposal.target or next(v.target for v in proposal.versions if v.target is not None)
        lines = [f"### `{proposal.lineage}` {address}", "",
                 "**At the base:**" if proposal.target else "**New — not at the base.**"]
        if proposal.target:
            lines += ["", _fenced(proposal.text)]
        for version in proposal.versions:
            where = "removed it" if version.target is None else (
                f"moved it to `{version.target}`" if version.target != proposal.target else "changed it")
            lines += ["", f"**{version.candidate}** {where}:"]
            for change in version.changes:
                reason = (change.reason or "").strip().replace("\n", "\n  ")
                lines.append(f"- `{(change.commit or '')[:10]}` ({change.kind}) {reason}")
            if version.ratings:
                cited = ", ".join(f"{rating.value} (`{rating.item_id}`)" for rating in version.ratings)
                lines.append(f"- its pipeline rated this section: {cited}")
            if version.target is not None:
                lines += ["", _fenced(version.text)]
        blocks.append("\n".join(lines))
    listed = "\n\n".join(blocks) or "(no candidate changed anything)"
    return (f"{INSTRUCTIONS}\n\n## Task\n\n{prompt}\n\n## Proposals\n\n{listed}\n\n## The configuration\n\n"
            f"{WORKSPACE.format(workspace=workspace)}\n\n## Committing\n\n{COMMITTING}")
