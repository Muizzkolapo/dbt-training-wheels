"""One conversation's conversion: two paths, a dialect, and the answers given
so far. No I/O of its own beyond the pipeline's, and no conversion logic --
`dbtw.web` imports `dbtw.core` exactly as `dbtw.cli` does (spec section 4).
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dbtw.core.assemble import ProjectChange, UnknownAnswerError, assemble
from dbtw.core.context import read_project
from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import Answer, Decision, answer_for, run_passes


@dataclass(frozen=True, slots=True)
class SessionView:
    """Everything one screen of the walk needs, gathered from one pair of runs.

    `change` is the conversion to render, `questions` its Decisions that ask
    something, and `prompts` is `{Decision.key: {Option.kind: columns_prompt}}`
    read off the pristine question -- the requirement an answer of that kind
    must actually meet, which is not what the rendered options say once an
    answer has been applied (see `Session.columns_prompts`).

    It exists for cost, and the cost is real: every accessor on `Session` is a
    whole conversion, so a screen that asked for the change, the questions and
    a prompt set per question paid 2 + N runs for N questions. This is two,
    whatever N is. It computes nothing the accessors do not -- a screen built
    from it and a screen built from them are the same screen.
    """

    change: ProjectChange
    questions: tuple[Decision, ...]
    prompts: dict[str, dict[str, str]]


@dataclass(frozen=True)
class Session:
    """One conversation about one SQL script and one dbt project.

    Held in memory and keyed by nothing: this is a local single-user tool, and
    a second tab is a second conversation over the same inputs (spec 4.1).

    `project` and `sql` are paths rather than anything read from them, and
    they cannot move for the life of the session -- the dataclass is frozen,
    which is what enforces that rather than this paragraph. A tier-2
    `Decision.key`
    embeds the path of the file its statement was read from
    ("tier2.append.<source_file>:<index>", spec 11.7), so a session that
    copied its input to a fresh directory per run would hand out keys the next
    run has never issued, and `assemble` would refuse every answer held
    against them with `UnknownAnswerError`. The refusal is loud, which is the
    engine behaving correctly, but it makes the answer loop unusable. A later
    slice that accepts an uploaded file has to derive one durable location for
    it, not a per-run temporary directory.

    `answers` is half of the user's contribution, and it records
    `(Option.kind, columns)` -- never a label. One answer has two spellings:
    the pristine question offers "merge on a unique key, checked on every
    run", and the Decision rebuilt once that answer is applied offers "merge
    on order_id, checked on every run" for the same choice. A session that
    recorded prose this engine rewrites underneath it broke on the second
    answer to any model; `kind` is a field, and `passes.answer_for` is the
    reader that turns it back into the label a given Decision offers.

    `descriptions` is the other half of the user's contribution, and it is the
    half no engine could have written: what a model is *for*. It is keyed by
    final model name rather than by a Decision key, because a description
    answers no question -- there is no Decision to key it to, and inventing
    one would put a choice on the caveats screen that nobody made.

    Frozen does not make it immutable: `answers` and `descriptions` are both
    dicts, and `answer` and `describe` fill them in place. What is pinned is
    the pair of paths, because everything else is read from them on demand.

    The other side of reading them on demand is that an edit to the SQL can
    strand a standing answer -- see `stale_answers`, which is how a consumer
    finds out and how it gets out.

    Nothing here is cached. Every call re-runs ingest -> classify ->
    run_passes -> assemble, because spec 4.3 requires the answer loop to re-run
    the pipeline rather than patch an assembled result -- a second code path
    applying answers to an assembled change is free to drift from the one the
    CLI takes, and can show a screen the CLI would never produce. The
    measurement behind that (a full run at 17 ms for 8 statements, 149 ms for
    80 producing 80 models) is also why there is no cache on the way in: it
    buys milliseconds and costs the guarantee that what is on screen is what
    these two paths currently hold.
    """

    project: Path
    sql: Path
    dialect: str | None = None
    answers: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    def current(self) -> ProjectChange:
        """This conversion with `answers` applied. What a screen renders.

        One pipeline run while no answer is held, two once one is: resolving
        an answer needs the pristine question set, and that is a whole
        conversion of its own. `view` is the accessor to reach for when a
        screen needs more than this.
        """
        if not self.answers:
            return self._run(None, self.descriptions)
        return self._run(self._resolve(self._pristine()), self.descriptions)

    def view(self) -> SessionView:
        """One screen: the change, its questions, and every question's column
        requirements. One pipeline run, or two once an answer is held.

        The same numbers as `current` alone, and the reason this exists: the
        prompts come off the pristine run the answers were resolved against,
        which has already been computed by the time they are needed.
        """
        pristine = self._pristine()
        resolved = self._resolve(pristine)
        # A run with no answers and no descriptions *is* the pristine run --
        # same inputs, same deterministic pipeline. Reusing it here is not a
        # cache: it is one value computed once inside one call, and it is gone
        # when the call returns. Descriptions are in that condition because
        # `_pristine` carries none: reusing it for a session that holds one
        # would render a screen with the reader's own words missing from it.
        described = bool(resolved or self.descriptions)
        change = self._run(resolved, self.descriptions) if described else pristine
        return SessionView(
            change=change,
            questions=tuple(d for d in change.decisions if d.question),
            prompts={
                decision.key: {option.kind: option.columns_prompt for option in decision.options}
                for decision in pristine.decisions
                if decision.question
            },
        )

    def questions(self) -> tuple[Decision, ...]:
        """The Decisions of the current run that ask the user something, in
        pipeline order.

        One pipeline run, or two once an answer is held.

        `question != ""` and nothing else (spec 3.3). A Tier-2 Decision is not
        necessarily a question -- `assemble`'s per-reference rewrite Decision
        is tier 2 and carries a `chosen` summarising what it did, which no one
        chose -- so keying on `tier`, or on the presence of `chosen`, offers
        the user something that was never asked.
        """
        return tuple(d for d in self.current().decisions if d.question)

    def columns_prompts(self, key: str) -> dict[str, str]:
        """For each kind this question offers, the `columns_prompt` an answer
        of that kind must satisfy -- empty where the option settles itself.

        One pipeline run, for one question. A screen asking this per question
        pays one conversion per question; `view` carries the same thing for
        all of them out of a run it had to make anyway.

        Read off the *pristine* question, which is the one an answer is sent
        against, and that is the whole reason this exists. After the first
        answer, the rebuilt Decision a screen is rendering spells its merge
        options with the key they were answered with, and a keyed option
        carries no prompt -- so a picker built from what is on screen would
        conclude that no columns are needed and send an answer `answer_for`
        refuses. The requirement belongs to the pristine option, and this is
        the only honest source for it.

        Keyed by `Option.kind` rather than by label for the same reason
        `answers` is: the labels move. A question that offered two options of
        one kind would collapse here, which no site builds and `answer_for`
        refuses outright at the moment such an answer is sent.
        """
        return {
            option.kind: option.columns_prompt
            for option in _question(self._pristine(), key).options
        }

    def stale_answers(self) -> tuple[str, ...]:
        """The held answers this conversion has no question for, in the order
        they were given. One pipeline run.

        Empty through every ordinary conversation. It fills when the SQL
        changes under a standing answer: a tier-2 key embeds its statement's
        position in the file it was read from (spec 11.7), so an edit that
        moves or removes that statement retires the key, and every answer held
        against it is refused -- `UnknownAnswerError` behaving exactly as
        designed.

        Every other accessor raises while one is held, `answer` included,
        because resolving the held set is what it does before it can validate
        anything new. This one does not, and that is its whole point: a
        consumer meeting that refusal can say which answers it is about, and
        offer `drop_stale_answers`, instead of showing an error with no
        subject and no way on.
        """
        asked = {d.key for d in self._pristine().decisions if d.question}
        return tuple(key for key in self.answers if key not in asked)

    def drop_stale_answers(self) -> tuple[str, ...]:
        """Forget the answers `stale_answers` names, and return them.

        The way out, and deliberately the only one. Skipping stale answers on
        the way into a run instead would leave a user reading a conversion
        that quietly does not contain an answer they gave, and a screen has no
        way to know it is looking at one -- which is the silence this project
        does not keep. Dropping them is a thing that happened, so it is a
        thing a caller is told about and has to render.
        """
        stale = self.stale_answers()
        for key in stale:
            del self.answers[key]
        return stale

    def answer(self, key: str, kind: str, columns: tuple[str, ...] = ()) -> None:
        """Record an answer of `kind` to the question `key`, or refuse.

        `kind` and `columns` are resolved against the *pristine* run -- the one
        with no answers -- and never against `current()`. The pristine
        question set never changes, so the label `answer_for` produces is
        always the one `assemble` validates against, and the keyed/keyless
        split stops being the caller's problem: an append question's merge
        options are keyless and require columns, a merge question's already
        name their key and refuse them, and which of those a screen happens to
        be displaying is not the same question as which the answer goes to.

        Four refusals, all loud, and none of them leaves the answer recorded:

        - a key this conversion does not carry;
        - a key whose Decision asks nothing (spec 3.3 again -- `tier` and
          `chosen` both lie about this);
        - a kind the pristine question does not offer, or columns it cannot
          use, both from `answer_for`;
        - an answer the assembler itself refuses.

        All four are found the same way -- by recording the answer and running
        the conversion it produces -- rather than by a gate in front of each,
        because only the last one can be found any other way and a gate that
        repeated the other three would be a second copy of rules that already
        refuse loudly. Running it is also the only thing that finds the last:
        `answer_for` accepts two columns for a checked merge (the option asked
        for columns and got some), and the one-column limit of dbt's `unique`
        test is enforced where the answer is applied. Recorded and left for
        the next render, one such answer would make every subsequent
        `current()` raise and the conversation would be over with no way back.
        Recorded, tried, and rolled back, it is a refusal the user can answer
        again.

        Two pipeline runs: the pristine one that resolves the answers, and the
        one that applies them. A stale key held from an earlier edit to the
        SQL refuses this too -- the whole set is resolved before any of it is
        validated. See `stale_answers`.
        """
        previous = dict(self.answers)
        # Mutated in place rather than rebound, restored the same way:
        # `answers` may be a dict the caller still holds, and a caller
        # watching its own dict must see what the session recorded and
        # nothing it refused.
        self.answers[key] = (kind, columns)
        try:
            # With the descriptions, so this validates the conversion the
            # reader will be looking at rather than a narrower one. No answer
            # this engine offers changes which models a conversion builds, so
            # the two runs agree today; the asymmetry is what would not
            # survive an answer that did, and it would not survive it quietly
            # -- the answer would be accepted and every render afterwards
            # would raise `UnknownModelError`, with the description that
            # became impossible never named.
            self._run(self._resolve(self._pristine()), self.descriptions)
        except Exception:
            self.answers.clear()
            self.answers.update(previous)
            raise

    def describe(self, model: str, text: str) -> None:
        """Record what `model` is for, in the reader's own words, or refuse.

        The one thing in this walk the engine cannot derive and does not try
        to. What a model is *for* is not in the SQL that builds it -- the SQL
        says what it computes -- so a description is text only a reader can
        write, and a suggested one would be this tool putting words in their
        mouth on a screen that asks for theirs.

        Blank text is recorded rather than deleted, and that is deliberate.
        `assemble` drops a blank description from the change it builds, so an
        emptied box reaches no .yml either way; what recording it buys is that
        clearing a description goes through the same refusal as writing one. A
        `pop` would accept a blank for a model this conversion does not build
        and say nothing, which is the one outcome this loop exists to prevent.

        Two pipeline runs, the same two `answer` makes and for the same
        reason: the model names a description is validated against belong to
        the run that applies the held answers, and the answers are resolved
        against the pristine one. Applied before it is kept, so a refused
        description is not held -- the next render would otherwise raise
        `UnknownModelError` for every screen, with no way back.
        """
        previous = dict(self.descriptions)
        # Mutated in place rather than rebound, restored the same way, for the
        # reason `answer` gives: a caller watching its own dict must see what
        # the session recorded and nothing it refused.
        self.descriptions[model] = text
        try:
            self._run(self._resolve(self._pristine()), self.descriptions)
        except Exception:
            self.descriptions.clear()
            self.descriptions.update(previous)
            raise

    def stale_descriptions(self) -> tuple[str, ...]:
        """The descriptions held for models this conversion no longer builds,
        in the order they were written. One pipeline run.

        The description half of `stale_answers`, reached the same way: the SQL
        is re-read on every run, so an edit under a standing description can
        retire the model it was written against, and `assemble` then refuses
        the whole conversion -- `UnknownModelError` behaving exactly as
        designed, and every screen raising with it.

        Computed from a run carrying no descriptions at all, which is what
        makes it answerable while the ordinary ones are not. It still resolves
        the held answers, so a session stranded on both is stranded on its
        answers first: those cannot be resolved, and this raises the same
        `UnknownAnswerError` every other accessor does. That ordering is the
        honest one -- an answer that cannot be resolved is a question this
        conversion no longer asks, and the models it would have named are not
        knowable until it is dropped.
        """
        carried = {model.name for model in self._run(self._resolve(self._pristine())).models}
        return tuple(name for name in self.descriptions if name not in carried)

    def drop_stale_descriptions(self) -> tuple[str, ...]:
        """Forget the descriptions `stale_descriptions` names, and return them.

        The way out, and the only one, for the reason `drop_stale_answers`
        gives: skipping them on the way into a run would show a reader a
        conversion that quietly does not carry words they wrote, and dropping
        someone's own sentences is a thing they are told about.
        """
        stale = self.stale_descriptions()
        for name in stale:
            del self.descriptions[name]
        return stale

    def _pristine(self) -> ProjectChange:
        """This conversion with no answers at all.

        The run every answer is resolved against. Not exposed: a screen that
        rendered it would show prose the engine has already rewritten -- the
        keyless "merge on a unique key" for a model whose key the user named
        two answers ago -- and the only thing a caller needs from it is
        reachable through `answer` and `columns_prompts`.
        """
        return self._run(None)

    def _run(
        self,
        answers: Mapping[str, Answer] | None,
        descriptions: Mapping[str, str] | None = None,
    ) -> ProjectChange:
        """One conversion of these two paths, with `answers` and `descriptions`
        applied.

        `descriptions` is a parameter rather than read off `self`, and the
        default of none is the reason. Two callers want a run without them:
        `_pristine`, whose whole job is the question set an answer is resolved
        against, and `stale_descriptions`, which needs the model names of a run
        that a stranded description cannot refuse. A run that always carried
        them would make both raise `UnknownModelError` in exactly the state
        they exist to get a reader out of.
        """
        result = ingest(self.sql, self.dialect)
        state = run_passes(classify_statements(result), result.dialect)
        return assemble(
            state, read_project(self.project), answers=answers, descriptions=descriptions
        )

    def _resolve(self, pristine: ProjectChange) -> dict[str, Answer]:
        return {
            key: answer_for(_question(pristine, key), kind, columns)
            for key, (kind, columns) in self.answers.items()
        }


def _question(change: ProjectChange, key: str) -> Decision:
    """The Decision `key` names, if it is one this conversion asks."""
    decision = next((d for d in change.decisions if d.key == key), None)
    if decision is None:
        asked = ", ".join(sorted(d.key for d in change.decisions if d.question))
        raise UnknownAnswerError(
            f"no question with key {key!r} in this conversion; it asks "
            f"{asked or 'nothing'}. A key is only valid for a run that read the same "
            "SQL at the same path"
        )
    if not decision.question:
        raise UnknownAnswerError(
            f"{decision.key} asks no question ({decision.action}); it records what this "
            "conversion did, so there is no answer to give it"
        )
    return decision


class EmptySourceError(ValueError):
    """Nothing arrived to convert.

    Input-driven, and the one refusal the entry screen can produce: a reader
    pressed Convert with an empty box and no file chosen. Not a dbtw bug, so
    the screen renders it rather than letting it surface as a traceback --
    the same category as the refusals the answer loop already gives back.
    """


@dataclass
class Source:
    """Where this walk's SQL comes from, and the conversation over it.

    `session` is None until SQL arrives. `dbtw web` started without a
    SQL_PATH has nothing to convert yet, so the walk's screens have nothing
    to render and the entry screen is what a reader meets instead. Supplying
    the path on the command line simply means it is not None to begin with.

    Mutable, and the one mutable thing in this package: every other type here
    is frozen. It holds what changes -- which conversation the app is serving
    -- and nothing else, so the boundary between "the app's state" and "a
    conversation's state" is one object wide. A second tab is a second view
    of whatever it currently holds, which is what spec 4.1 asks for.

    `start` is the only way the session changes, and it replaces rather than
    edits: new SQL is a new conversation, not the old one with a different
    script underneath it. That is why no answer survives it -- an answer
    names a question of the run that asked it, and the new run did not ask.

    Pasted and uploaded SQL lands in one directory per app process, made on
    first use and reused after it. Stability is the point, not tidiness:
    spec 11.7 records that a tier-2 `Decision.key` embeds the path its
    statement was read from, so a source staged somewhere new per run hands
    out keys that are invalid on the next one and every answer held against
    them is refused. One directory for the life of the process is exactly as
    stable as the session that reads it, which is all the keys need.
    """

    project: Path
    out: Path
    dialect: str | None = None
    session: Session | None = None
    _staged: Path | None = field(default=None, repr=False)

    def start(self, files: Mapping[str, str]) -> None:
        """Stage `files` and open a conversation over them.

        `files` is {filename: text}. More than one is ordinary: `ingest`
        reads a directory as every .sql in it, so a folder of scripts and a
        single pasted query are the same shape here, and the session is
        pointed at the directory either way.

        The directory is emptied first. What a reader sends replaces what
        they sent before rather than joining it -- a second paste that left
        the first one in place would convert both and show a walk over a
        script the reader thinks they replaced.
        """
        if not files or not any(text.strip() for text in files.values()):
            raise EmptySourceError(
                "nothing to convert: paste a query or choose a .sql file, and press Convert"
            )
        staged = self._staging()
        for existing in staged.iterdir():
            existing.unlink()
        for name, text in files.items():
            (staged / name).write_text(text, encoding="utf-8")
        self.session = Session(project=self.project, sql=staged, dialect=self.dialect)

    def _staging(self) -> Path:
        if self._staged is None:
            self._staged = Path(tempfile.mkdtemp(prefix="dbtw-source-"))
        return self._staged
