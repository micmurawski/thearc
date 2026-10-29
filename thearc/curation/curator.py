"""Curation: a curator edits a MetaAgent's workspace in response to reflection items, one commit per change.

The curator works in a clone of the workspace at a base commit ``B``, whose markers give every
section's lineage ID; with ``scales``, its prompt adds every section's ratings so far. It edits
files directly and commits through thearc's ``commit_change`` tool — one change per commit, the
reason as its message — and ends with ``finish``. Recording the commits after ``B`` (or Kept
entries, when there are none) extends the journal; the candidate lands in the workspace as branch
``curation/<id>``. The current branch is left as it was — unless the agent is a worktree
(``agent.to_worktree``), which then moves forward to the curated result: a candidate's worktree
*is* its pipeline's outcome.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.reflection import Reflection
from thearc.reflection.mcp import ToolSpec, serve
from thearc.reflection.runtimes import RUNTIMES, ReflectionError, Runtime
from thearc.resources.agent import MetaAgent
from thearc.resources.changes import detect_changes, frozen_changes
from thearc.resources.git import _git, agent_at, branch_name, commit, commit_journal, is_worktree, record_commits
from thearc.resources.journal import Entry, Journal, Kept
from thearc.resources.ratings import RatingPolicy, RatingScale, since_last_modification
from thearc.resources.reflection import ReflectionItem
from thearc.resources.targets import STRUCTURED_KINDS, ResourceTarget
from thearc.resources.views import agent_outline
from thearc.resources.workspace import JOURNAL_FILE, MANIFEST, mark_new_targets, read_workspace

PROTECTED = (JOURNAL_FILE, MANIFEST, ".gitattributes")

INSTRUCTIONS = """You are curating an AI agent's configuration — its instructions, skills, hooks and other
files — in response to reviews of how the agent behaved. Each review item rates lines of the
configuration and explains why. Make the smallest changes that address what the items found:
rewrite, add, or delete what the evidence calls for. Keeping something unchanged is a valid outcome
when the items do not justify a change. Do not invent behaviour the items do not report."""

WORKSPACE = """The configuration is in `{workspace}`; edit its files there. You cannot run commands.

Every section heading, code file, and hooks/MCP entry carries a marker with the section's ID —
`<!-- res_… -->` after a heading, `# res_…` on a code file's first line, `// res_…` after a JSONC
entry's key. Keep a section's marker on its heading when you edit, move,
or rename it: that is how the section's history follows it. A new section needs no marker; deleting a
section deletes its marker; never copy a marker. `journal.jsonl` (the history of every section) may be
read but not edited; neither may `agent.json`."""

FROZEN = """These parts are frozen: they belong to the configuration but are not yours to change, and your edits to
them are undone: {patterns}."""

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
    """``commit_change`` and ``finish``, acting on the curator's clone. A person amending a workspace uses them too,
    with ``frozen_allowed``: frozen parts are only kept from curators and mergers."""

    def __init__(self, clone: Path, author: str, *, email: str = "curator@thearc.local", frozen_allowed: bool = False):
        self.clone, self.author, self.email, self.frozen_allowed = clone, author, email, frozen_allowed
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

    def _discard_frozen(self) -> list[str]:
        """Restore the files of frozen resources the edits touched; refuse edits to frozen sections of other files."""
        before = agent_at(self.clone, "HEAD")
        if before is None or not before.frozen or self.frozen_allowed:
            return []
        try:
            after = read_workspace(self.clone, journal=False)
        except ValueError:
            return []  # The read-back check reports it.
        restored: list[str] = []
        for entry in frozen_changes(before, after):
            resource = entry.target.resource
            if not before.is_frozen(resource) or resource.kind in STRUCTURED_KINDS:
                continue
            owner = before if resource in before.files().targets else after
            path = owner.files().span(resource).path
            if path in restored:
                continue
            if path in before.files().files:
                _git(self.clone, "checkout", "-q", "HEAD", "--", path)
            else:
                (self.clone / path).unlink(missing_ok=True)
            restored.append(path)
        if left := frozen_changes(before, read_workspace(self.clone, journal=False)):
            raise ValueError("Nothing was committed: these are frozen and may not be changed; undo your edits to "
                             "them: " + ", ".join(str(entry.target) for entry in left))
        return restored

    def commit_change(self, message: str) -> str:
        if not message.strip():
            raise ValueError("The message must give the reason for the change.")
        discarded = self._discard_protected() + self._discard_frozen()
        note = f"{', '.join(discarded)} may not be edited; your edits to it were discarded. " if discarded else ""
        if not self._pending():
            raise ValueError(note + "There are no edits to commit.")
        try:
            read_workspace(self.clone, journal=False)
        except ValueError as exc:
            raise ValueError(f"{note}Nothing was committed: the configuration does not read back ({exc}). Fix it "
                             "and commit again.") from None
        if marked := mark_new_targets(self.clone):  # So a later rename is a move, not a new history.
            note += ("Marked " + ", ".join(f"{target} as {identifier}" for target, identifier in marked.items())
                     + "; keep these markers. ")
        before = agent_at(self.clone, "HEAD")
        _git(self.clone, "add", "-A")
        sha = commit(self.clone, message.strip(), author=self.author, email=self.email)
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

        With ``scales``, the prompt lists every section with its ratings so far on them (under
        ``policy``, default ``since_last_modification``). Uncommitted journal entries are committed,
        so the curator reads the current journal. ``extra`` is stored on every entry.
        """
        root = agent.checked_workspace("curate")
        policy = policy or since_last_modification
        outline = agent_outline(agent, scales, ids=True, policy=policy) if scales else None
        commit_journal(root)
        if dirty := [line for line in _git(root, "status", "--porcelain").decode().splitlines() if line.strip()]:
            raise ValueError(f"commit or undo the workspace's pending edits first: {dirty}")
        base = _git(root, "rev-parse", "HEAD").decode().strip()
        journal = Journal.load(root / JOURNAL_FILE)
        items = _items(reflections, journal)
        identifier = "curation_" + uuid.uuid4().hex
        branch = branch_name(root, "curation", identifier.removeprefix("curation_")[:12])
        stamp = {"curation": identifier, "curator": self.harness,
                 **({"model": self.runtime.model} if self.runtime.model else {}), **extra}
        with tempfile.TemporaryDirectory(prefix="thearc-curation-") as directory:
            clone = Path(directory).resolve() / "agent"
            _git(Path(directory), "clone", "-q", "--no-checkout", str(root), str(clone))
            _git(clone, "checkout", "-q", "-b", branch, base)
            tools = CurationTools(clone, f"curator ({self.harness})")
            with serve(tools.specs()) as endpoint, self.runtime.conversation(clone, endpoint, edit=True) as talk:
                talk.send(_message(prompt, reflections, items, clone, outline, agent.frozen))
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
            if is_worktree(root):  # A candidate's worktree becomes the curated result.
                _git(root, "merge", "-q", "--ff-only", branch)
        curator = {"harness": self.harness, "model": self.runtime.model, "session": talk.id}
        return Curation(identifier, root, base, branch, head, tuple(tools.commits), tuple(recorded), tools.summary,
                        curator, tuple(reflection.id for reflection in reflections))


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


def _message(prompt: str, reflections: Sequence[Reflection], items: list[_Item], workspace: Path,
             outline: str | None = None, frozen: Sequence[str] = ()) -> str:
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
    ratings = (f"\n\n## Ratings so far\n\nEvery section with its ID and the ratings of its current text:\n\n"
               f"{outline.rstrip()}") if outline else ""
    return (f"{INSTRUCTIONS}\n\n## Task\n\n{prompt}\n\n## Reflections\n\n{listed}\n\n## The configuration\n\n"
            f"{WORKSPACE.format(workspace=workspace)}{frozen_note(frozen)}{ratings}\n\n## Committing\n\n{COMMITTING}")


def frozen_note(frozen: Sequence[str]) -> str:
    return "\n\n" + FROZEN.format(patterns=", ".join(f"`{pattern}`" for pattern in frozen)) if frozen else ""
