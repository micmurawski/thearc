# The /evolve recipe: the last week's sessions → reflect on each → curate. Edit freely.
"""How a round of `thearc evolve` proposes a new version.

`thearc evolve` runs `propose(evolution)` and turns what it returns into the proposal. The round is a flow — find the
sessions, reflect on each, curate — so `evolution.track` can show it live at .thearc/cache/progress.html, with the
round's log (`evolution.log`) in a tab. The prompts are the Markdown files in prompts/ next to this script.
"""

from collections import Counter
from datetime import timedelta
from pathlib import Path

from thearc.curation import Curator
from thearc.evolve import SCHEMA, USEFULNESS, Evolution
from thearc.flow import BatchNode, Flow, Node
from thearc.reflection import ReflectionError, Reflector
from thearc.resources import MetaAgent

PROMPTS = Path(__file__).parent / "prompts"
REFLECT, CURATE = ((PROMPTS / f"{name}.md").read_text() for name in ("reflect", "curate"))
HARNESS = "claude"  # Whose sessions are read: the harness this project evolves (set at install).
RUNTIME = "claude"  # Runs every reflection and the curation.
WINDOW = timedelta(days=7)
MIN_SESSIONS = 3


class FindSessions(Node):
    """The harness's sessions run in this project in the last WINDOW; enough of them to learn from?"""

    def prep(self, shared):
        return shared["evolution"]

    def exec(self, evolution):
        return evolution.sessions(harness=HARNESS, since=WINDOW)  # Never one that ran /evolve.

    def post(self, shared, prep_res, sessions):
        evolution = shared["evolution"]
        evolution.log.info(f"{len(sessions)} {HARNESS} session(s) in the last {WINDOW.days} days")
        for session in sessions:
            evolution.log.info(f"  {session.id}  {session.metadata.started_at}  {session.metadata.title or ''}")
        if len(sessions) < MIN_SESSIONS:
            evolution.nothing(f"{len(sessions)} session(s) in the last {WINDOW.days} days; {MIN_SESSIONS} needed")
            return "too few"
        shared["sessions"] = sessions
        shared["candidate"] = evolution.candidate()  # A worktree of the installed release.
        return "enough"


class TooFew(Node):
    """Nothing to propose yet."""


class Reflect(BatchNode):
    """Each session, reviewed against the installed configuration: which lines helped, which hurt."""

    def prep(self, shared):
        self.evolution, self.candidate = shared["evolution"], shared["candidate"]
        return shared["sessions"]

    def exec(self, session):
        if self.evolution.reflected(session, self.candidate):  # A stopped round kept its reflections.
            self.evolution.log.info(f"{session.id}: reflected on in an earlier, stopped run")
            return
        self.evolution.log.info(f"{session.id}: reflecting with {RUNTIME}…")
        try:
            reflection = Reflector(RUNTIME).handoff(REFLECT, session, self.candidate, SCHEMA,
                                                    provenance=self.evolution.provenance(session))
        except ReflectionError as error:  # One failed reflection costs its session, not the round.
            self.evolution.log.warning(f"{session.id}: no reflection ({error}); going on without it")
            return
        self.candidate.add_reflection(reflection)
        rated = Counter(item.rating for item in reflection)
        self.evolution.log.info(f"{session.id}: {len(reflection)} rating(s) "
                                f"({', '.join(f'{value} {label}' for label, value in rated.items()) or 'none'})")


class Curate(Node):
    """The configuration changed where the reflections show it hurt, and kept where they show it helped."""

    def prep(self, shared):
        return shared["evolution"], shared["candidate"]

    def exec(self, prep_res):
        evolution, candidate = prep_res
        if not evolution.curated(candidate):
            reflections = evolution.reflections(candidate)
            if not reflections:
                evolution.nothing("no reflection succeeded")
                return None
            evolution.log.info(f"curating with {RUNTIME} from {len(reflections)} reflection(s)…")
            curation = Curator(RUNTIME).curate(CURATE, reflections, candidate, scales=[USEFULNESS])
            for commit in curation.commits:
                evolution.log.info(f"  {commit.sha[:10]} {commit.message.splitlines()[0]}")
            evolution.log.info(f"curator: {curation.summary}")
        return candidate.reload()

    def post(self, shared, prep_res, proposal):
        if proposal is not None:
            shared["proposal"] = proposal


def propose(evolution: Evolution) -> MetaAgent | None:
    find, too_few, reflect, curate = (
        FindSessions(metadata={"harness": HARNESS, "window": f"{WINDOW.days} days", "min": MIN_SESSIONS}),
        TooFew(),
        Reflect(metadata={"runtime": RUNTIME, "prompt": "prompts/reflect.md"}),
        Curate(metadata={"runtime": RUNTIME, "prompt": "prompts/curate.md"}),
    )
    find - "too few" >> too_few
    find - "enough" >> reflect >> curate
    shared = evolution.track(Flow(start=find), {"evolution": evolution})
    return shared.get("proposal")
