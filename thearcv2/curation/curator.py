"""Curation: a curator edits a MetaAgent's workspace in response to reflection items, one commit per change.

The curator works in a clone of the workspace at a base commit ``B`` (annotated, so it sees every
section's lineage ID and ratings). It edits files directly and commits through thearc's
``commit_change`` tool — one change per commit, the reason as its message — and ends with
``finish``. Recording the commits after ``B`` (or Kept entries, when there are none) extends the
journal; the candidate lands in the workspace as branch ``curation/<id>``, leaving the current
branch untouched.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearcv2.reflection import Reflection
from thearcv2.reflection.mcp import ToolSpec, serve
from thearcv2.reflection.runtimes import RUNTIMES, ReflectionError, Runtime
from thearcv2.resources.agent import MetaAgent
from thearcv2.resources.changes import detect_changes
from thearcv2.resources.git import _git, agent_at, commit, record_commits
from thearcv2.resources.journal import Entry, Journal, Kept
from thearcv2.resources.ratings import RatingPolicy, RatingScale
from thearcv2.resources.reflection import ReflectionItem
from thearcv2.resources.targets import ResourceTarget
from thearcv2.resources.workspace import JOURNAL_FILE, MANIFEST, read_workspace

PROTECTED = (JOURNAL_FILE, MANIFEST, ".gitattributes")

INSTRUCTIONS = """You are curating an AI agent's configuration — its instructions, skills, hooks and other
files — in response to reviews of how the agent behaved. Each review item rates lines of the
configuration and explains why. Make the smallest changes that address what the items found:
rewrite, add, or delete what the evidence calls for. Keeping something unchanged is a valid outcome
when the items do not justify a change. Do not invent behaviour the items do not report."""

WORKSPACE = """The configuration is in `{workspace}`; edit its files there. You cannot run commands.

Every section heading, code file, and hooks/MCP entry carries a marker with the section's ID and its
ratings so far — `<!-- res_… · usefulness: (…) -->` after a heading, `# res_…` on a code file's first
line, `// res_…` after a JSONC entry's key. Keep a section's marker on its heading when you edit, move,
or rename it: that is how the section's history follows it. A new section needs no marker; deleting a
section deletes its marker; never copy a marker. `journal.jsonl` (the history of every section) may be
read but not edited; neither may `agent.json`."""

COMMITTING = """Commit every change with `commit_change(message)`: it commits everything you edited since the
last commit. Make **one change per commit** — one section or resource — and write **the reason as the
message**: what was wrong, citing the ratings it answers (e.g. `reflection_…#/ratings/2`), and what the
change does. When you are done, call `finish(summary)`. If you changed nothing, the summary is your
reason for keeping the configuration as it is."""


class CurationError(ReflectionError):
    """A curation could not be completed; ``code`` classifies why."""


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    changed: tuple[str, ...]  # The targets it changed, e.g. ``Modified skill:retry#Retry > Limits``.


@dataclass(frozen=True)
class Curation:
    """One curator run: its candidate branch, commits, and the journal entries recording them."""

    id: str
    workspace: Path
    base: str
    branch: str
    head: str
    commits: tuple[Commit, ...]
    entries: tuple[Entry, ...]
    summary: str
    curator: dict[str, Any]
    reflections: tuple[str, ...] = ()
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def changed(self) -> bool:
        return bool(self.commits)

    def candidate(self) -> MetaAgent:
        """The curated configuration, as committed on the candidate branch."""
        agent = agent_at(self.workspace, self.head)
        if agent is None:
            raise ValueError(f"no workspace at {self.head}")
        return agent


class CurationTools:
    """``commit_change`` and ``finish``, acting on the curator's clone."""

    def __init__(self, clone: Path, author: str):
        self.clone, self.author = clone, author
        self.commits: list[Commit] = []
        self.summary: str | None = None

    def _pending(self) -> list[str]:
        status = _git(self.clone, "status", "--porcelain", "-uall").decode()
        return [line[3:].split(" -> ")[-1].strip('"') for line in status.splitlines() if line.strip()]

    def _discard_protected(self) -> list[str]:
        touched = [path for path in self._pending() if path in PROTECTED]
        for path in touched:
            _git(self.clone, "checkout", "-q", "HEAD", "--", path)
        return touched

    def commit_change(self, message: str) -> str:
        if not message.strip():
            raise ValueError("The message must give the reason for the change.")
        discarded = self._discard_protected()
        note = f"{', '.join(discarded)} may not be edited; your edits to it were discarded. " if discarded else ""
        if not self._pending():
            raise ValueError(note + "There are no edits to commit.")
        try:
            read_workspace(self.clone, journal=False)
        except ValueError as exc:
            raise ValueError(f"{note}Nothing was committed: the configuration does not read back ({exc}). Fix it "
                             "and commit again.") from None
        before = agent_at(self.clone, "HEAD")
        _git(self.clone, "add", "-A")
        sha = commit(self.clone, message.strip(), author=self.author, email="curator@thearc.local")
        after = agent_at(self.clone, sha)
        changed = tuple(f"{entry.kind.capitalize()} {entry.target}"
                        for entry in (detect_changes(before, after) if after is not None else []))
        self.commits.append(Commit(sha, message.strip(), changed))
        reply = f"{note}Committed {sha[:10]}: " + ("; ".join(changed) or "no configuration change (markers only)")
        if len(changed) > 1:
            reply += (". This commit changed several targets; make one change per commit unless they are one "
                      "change.")
        return reply

    def finish(self, summary: str) -> str:
        if not summary.strip():
            raise ValueError("The summary must not be empty.")
        self._discard_protected()
        if pending := self._pending():
            raise ValueError("Commit or undo your remaining edits first: " + ", ".join(pending))
        self.summary = summary.strip()
        return "Curation finished. End your turn."

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(self.commit_change, "Commit everything edited since the last commit as one change. "
                     "`message` is the reason for the change.", read_only=False),
            ToolSpec(self.finish, "Finish the curation once every change is committed. `summary` sums up what "
                     "you changed, or why you kept the configuration as it is.", read_only=False),
        ]


class Curator:
    """A curator: a harness and a model.

        curator = Curator("codex")
        curation = curator.curate(PROMPT, reflections, agent, scales=[usefulness])
        curation.branch, curation.commits, curation.entries
    """

    def __init__(self, harness: str = "codex", *, model: str | None = None, effort: str | None = None,
                 timeout: float = 1800, runtime: Runtime | None = None):
        if runtime is None:
            if harness not in RUNTIMES or harness == "antigravity":
                raise ValueError(f"no curator runtime for {harness!r}; available: codex, claude, pi")
            runtime = RUNTIMES[harness](model=model, effort=effort, timeout=timeout)
        self.runtime = runtime

    @property
    def harness(self) -> str:
        return self.runtime.harness

    def curate(
        self,
        prompt: str,
        reflections: Sequence[Reflection],
        agent: MetaAgent,
        *,
        scales: RatingScale | Sequence[RatingScale] = (),
        policy: RatingPolicy | None = None,
        **extra: Any,
    ) -> Curation:
        """Curate ``agent``'s workspace in response to ``reflections`` (already added to its journal).

        With ``scales``, the markers' ratings are refreshed first. Uncommitted journal entries are
        committed, so the curator reads the current journal. ``extra`` is stored on every entry.
        """
        root = agent.checked_workspace("curate")
        if scales:
            agent.annotate(scales, policy=policy)
        _commit_journal(root)
        if dirty := [line for line in _git(root, "status", "--porcelain").decode().splitlines() if line.strip()]:
            raise ValueError(f"commit or undo the workspace's pending edits first: {dirty}")
        base = _git(root, "rev-parse", "HEAD").decode().strip()
        journal = Journal.load(root / JOURNAL_FILE)
        items = _items(reflections, journal)
        identifier = "curation_" + uuid.uuid4().hex
        branch = f"curation/{identifier.removeprefix('curation_')[:12]}"
        stamp = {"curation": identifier, "curator": self.harness,
                 **({"model": self.runtime.model} if self.runtime.model else {}), **extra}
        with tempfile.TemporaryDirectory(prefix="thearc-curation-") as directory:
            clone = Path(directory).resolve() / "agent"
            _git(Path(directory), "clone", "-q", "--no-checkout", str(root), str(clone))
            _git(clone, "checkout", "-q", "-b", branch, base)
            tools = CurationTools(clone, f"curator ({self.harness})")
            with serve(tools.specs()) as endpoint, self.runtime.conversation(clone, endpoint, edit=True) as talk:
                talk.send(_message(prompt, reflections, items, clone))
            if tools.summary is None:
                raise CurationError("no_finish", f"The curator ended without calling finish "
                                    f"({len(tools.commits)} commit(s) made)")
            recorded: list[Entry] = list(record_commits(clone, journal, since=base, **stamp))
            if not recorded:
                revision = read_workspace(clone, journal=False).revision
                recorded = [Kept(target=target, revision=revision, reason=tools.summary, extra=stamp)
                            for target in _kept_targets(items, journal)]
                journal.record(*recorded)
            if recorded:
                Journal.append(clone / JOURNAL_FILE, recorded)
                _git(clone, "add", JOURNAL_FILE)
                commit(clone, f"Record {identifier}")
            head = _git(clone, "rev-parse", "HEAD").decode().strip()
            _git(root, "fetch", "-q", str(clone), f"{branch}:{branch}")
        curator = {"harness": self.harness, "model": self.runtime.model, "session": talk.id}
        return Curation(identifier, root, base, branch, head, tuple(tools.commits), tuple(recorded), tools.summary,
                        curator, tuple(reflection.id for reflection in reflections))


def _commit_journal(root: Path) -> None:
    """Commit journal entries appended since the last commit (e.g. by ``add_reflection``)."""
    if _git(root, "status", "--porcelain", "--", JOURNAL_FILE).strip():
        _git(root, "add", JOURNAL_FILE)
        commit(root, "Record ratings")


@dataclass(frozen=True)
class _Item:
    reflection: Reflection
    item: ReflectionItem
    targets: tuple[tuple[ResourceTarget, str, bool], ...]  # (current address, lineage ID, removed since)


def _items(reflections: Sequence[Reflection], journal: Journal) -> list[_Item]:
    """Each reflection item with the targets its ratings landed on, followed to their current addresses."""
    where: dict[str, tuple[ResourceTarget, str, bool]] = {}
    for lineage in journal.lineages():
        for entry in lineage.entries:
            where[entry.id] = (lineage.target, lineage.id, lineage.removed)
    by_item: dict[tuple[str, str], list[tuple[ResourceTarget, str, bool]]] = {}
    for rating in journal.ratings():
        by_item.setdefault((rating.item_id, rating.scale), []).append(where[rating.id])
    items = []
    for reflection in reflections:
        for item in reflection.items:
            targets = by_item.get((reflection.item_id(item), item.scale))
            if targets is None:
                raise ValueError(f"{reflection.id} is not in the journal; add it with agent.add_reflection first")
            items.append(_Item(reflection, item, tuple(dict.fromkeys(targets))))
    return items


def _kept_targets(items: list[_Item], journal: Journal) -> list[ResourceTarget]:
    live = set(journal.ids())
    return list(dict.fromkeys(target for item in items for target, _, removed in item.targets
                              if not removed and target in live))


def _message(prompt: str, reflections: Sequence[Reflection], items: list[_Item], workspace: Path) -> str:
    blocks = []
    for reflection in reflections:
        lines = [f"### `{reflection.id}` (on revision {reflection.revision})", "",
                 reflection.to_markdown(level=4).rstrip()]
        rated = [item for item in items if item.reflection is reflection]
        if rated:
            lines += ["", "Its ratings landed on these sections (ID, current address):"]
        for entry in rated:
            where = ", ".join(f"{identifier} {target}" + (" (since removed)" if removed else "")
                              for target, identifier, removed in entry.targets)
            lines.append(f"- `{reflection.item_id(entry.item)}` {entry.item.scale} **{entry.item.rating}** "
                         f"(`{entry.item.ref}`): {where}")
        blocks.append("\n".join(lines))
    listed = "\n\n".join(blocks) or "(no reflections)"
    return (f"{INSTRUCTIONS}\n\n## Task\n\n{prompt}\n\n## Reflections\n\n{listed}\n\n## The configuration\n\n"
            f"{WORKSPACE.format(workspace=workspace)}\n\n## Committing\n\n{COMMITTING}")
