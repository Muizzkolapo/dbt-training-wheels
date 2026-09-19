"""One conversation's conversion: two paths, a dialect, and the answers given
so far. No I/O of its own beyond the pipeline's, and no conversion logic --
`dbtw.web` imports `dbtw.core` exactly as `dbtw.cli` does (spec section 4).
"""

from __future__ import annotations

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

    `answers` is the whole of the user's contribution, and it records
    `(Option.kind, columns)` -- never a label. One answer has two spellings:
    the pristine question offers "merge on a unique key, checked on every
    run", and the Decision rebuilt once that answer is applied offers "merge
    on order_id, checked on every run" for the same choice. A session that
    recorded prose this engine rewrites underneath it broke on the second
    answer to any model; `kind` is a field, and `passes.answer_for` is the
    reader that turns it back into the label a given Decision offers.

    Frozen does not make it immutable: `answers` is a dict and `answer` fills
    it in place. What is pinned is the pair of paths, because everything else
    is read from them on demand.

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

    def current(self) -> ProjectChange:
        """This conversion with `answers` applied. What a screen renders.

        One pipeline run while no answer is held, two once one is: resolving
        an answer needs the pristine question set, and that is a whole
        conversion of its own. `view` is the accessor to reach for when a
        screen needs more than this.
        """
        if not self.answers:
            return self._run(None)
        return self._run(self._resolve(self._pristine()))

    def view(self) -> SessionView:
        """One screen: the change, its questions, and every question's column
        requirements. One pipeline run, or two once an answer is held.

        The same numbers as `current` alone, and the reason this exists: the
        prompts come off the pristine run the answers were resolved against,
        which has already been computed by the time they are needed.
        """
        pristine = self._pristine()
        resolved = self._resolve(pristine)
        # A run with no answers *is* the pristine run -- same inputs, same
        # deterministic pipeline. Reusing it here is not a cache: it is one
        # value computed once inside one call, and it is gone when the call
        # returns.
        change = self._run(resolved) if resolved else pristine
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
            self._run(self._resolve(self._pristine()))
        except Exception:
            self.answers.clear()
            self.answers.update(previous)
            raise

    def _pristine(self) -> ProjectChange:
        """This conversion with no answers at all.

        The run every answer is resolved against. Not exposed: a screen that
        rendered it would show prose the engine has already rewritten -- the
        keyless "merge on a unique key" for a model whose key the user named
        two answers ago -- and the only thing a caller needs from it is
        reachable through `answer` and `columns_prompts`.
        """
        return self._run(None)

    def _run(self, answers: Mapping[str, Answer] | None) -> ProjectChange:
        result = ingest(self.sql, self.dialect)
        state = run_passes(classify_statements(result), result.dialect)
        return assemble(state, read_project(self.project), answers=answers)

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
