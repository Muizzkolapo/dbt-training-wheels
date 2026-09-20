"""Data shapes for the pass pipeline, and the two readers that know one of
their fields' own contract (`statement_index`, `answer_for`). No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dbtw.core.ingest.types import ClassifiedStatement

Tier = Literal[1, 2, 3]

# Which answer an Option is, independent of how it is currently worded. One
# value per answer this tool offers, and a Literal rather than a str so a
# consumer switching on it is exhaustive and a typo is a type error rather
# than an option silently never matching. Widen it when a sixth answer is
# offered, so adding one is a decision rather than a spelling.
OptionKind = Literal["append", "merge", "merge_checked", "inline", "var"]


@dataclass(frozen=True, slots=True)
class Option:
    """One answer a Tier-2 question offers, and what taking it does.

    `label` is the answer as both the report and a button name it; `effect`
    is what dbt will do if it is taken, in dbt's own terms. The effect is
    written here rather than by a consumer because the report and the web
    UI render from the same records and must not explain a choice
    differently — see RFC section 9.

    `kind` is which answer this is, and unlike `label` it does not move. One
    answer has two spellings: the question the passes hand out offers "merge
    on a unique key, checked on every run", and the Decision rebuilt once
    that answer is applied offers "merge on order_id, checked on every run"
    for the same choice. A consumer that recorded which option a user took by
    its label was recording prose this engine rewrites underneath it, and
    broke on the second answer to any model; recording `kind` is reading a
    field. It carries no default: every option is one of these answers, and a
    factory that had to be *remembered* to stamp would hand a consumer a
    confident wrong identity rather than an error. `answer_for` below is the
    reader that turns a kind back into the label a given Decision offers.

    `columns_prompt` is the extra input the option cannot settle without,
    named in the wording every consumer shows above the field it asks for
    it in — "merge on a unique key" says nothing about which key. Empty
    means the label settles the choice by itself, and columns handed to
    such an option are refused rather than discarded. It carries the
    requirement and the wording in one field for the same reason `effect`
    is written here: a bare boolean would leave every consumer inventing
    its own prompt, which is the label-copying this record exists to
    remove, one step removed.

    `plain` says what `effect` says to a reader who has never used dbt;
    the two are reviewed together in the report so they cannot drift.

    `declares_test` names the dbt test taking this option asks for on the
    columns it is answered with — empty for an option that asks for none.
    It travels on the Option for the same reason `columns_prompt` does: the
    alternative is every consumer, and the assembler applying the answer,
    carrying its own copy of the one label that means "and have dbt check
    it", which is the label-copying these records exist to remove. It also
    pins *which* test: a run can only record the test an option put on the
    table, so no test the user was never offered can be written beside
    their model.
    """

    label: str
    effect: str
    # Ordered ahead of the defaulted fields so it can have no default of its
    # own: a wrong-but-plausible one ("append") is the failure this field
    # exists to remove, and it would be invisible until a user's answer came
    # back as the wrong choice.
    kind: OptionKind
    columns_prompt: str = ""
    plain: str = ""  # the same consequence, assuming no dbt knowledge
    # Literal rather than str: this value is copied onto a SchemaTest and
    # rendered into a .yml as a dbt test name, so a typo would be a file dbt
    # rejects. Widen it deliberately when a second test is offered, so
    # adding one is a decision rather than a spelling.
    declares_test: Literal["", "unique"] = ""


def append_option() -> Option:
    """The 'append every row' answer, worded once for every question that offers it."""
    return Option(
        label="append every row",
        kind="append",
        effect=(
            "Every run re-inserts everything this model selects. Rows already in the "
            "table stay where they are, so a second run duplicates them unless the "
            "SELECT filters to new rows itself."
        ),
        plain=(
            "Every time this runs it adds everything it finds, on top of what is "
            "already there. Run it twice and you get two copies of every row, "
            "unless the query itself only asks for new ones."
        ),
    )


def merge_option(keys: tuple[str, ...] = ()) -> Option:
    """The 'merge on a key' answer. Names the keys when they are known — read
    off a MERGE's ON clause — and stays generic when the user has yet to pick.
    """
    if not keys:
        # Both registers used to say a merge with an empty unique_key "fails
        # at dbt run time". This engine never runs dbt, and the claim is not
        # one it can stand behind: dbt-core's merge macro appears to
        # substitute a false join predicate and drop the matched branch when
        # the key is empty, which appends rather than errors. Neither reading
        # was verifiable here, so both now say only the consequence that
        # holds whichever it is -- with no key there is nothing to match on,
        # so nothing gets updated. That is still the cost the reader needs
        # before leaving the column blank, without asserting a failure mode
        # nobody checked, or which branch dbt actually takes when there is
        # nothing to match.
        return Option(
            label="merge on a unique key",
            kind="merge",
            effect=(
                "Each run updates the row whose key matches and inserts the rows that "
                "match nothing. Needs a column that identifies a row uniquely — with an "
                "empty unique_key there is nothing to match on, so nothing gets updated."
            ),
            # The one option whose label leaves its key unsaid, so the one
            # option that needs columns supplied with the answer.
            columns_prompt="the column(s) that identify a row uniquely",
            plain=(
                "Each run compares that column's value against what is already in "
                "the table -- a match updates the existing row, and everything "
                "else gets added, so nothing is duplicated. It needs a column "
                "whose value is different on every row -- an id -- and without "
                "one there is nothing to compare against, so nothing gets updated."
            ),
        )
    named = ", ".join(keys)
    return Option(
        label=f"merge on {named}",
        # The same answer as the keyless branch above, spelled with the key it
        # was built for -- see `kind` on Option.
        kind="merge",
        effect=(
            f"Updates the row whose {named} matches, with every column this model "
            "selects, and inserts rows matching none."
        ),
        plain=(
            f"Each run finds the row whose {named} matches and updates it, and "
            "adds the rows that match nothing. Rows are not duplicated."
        ),
    )


def verify_option(keys: tuple[str, ...] = ()) -> Option:
    """The 'merge, and have dbt check the key' answer. The check is dbt's
    built-in `unique` test, declared in a .yml file beside the model. It is
    only offered where it can be honoured: the test checks one column, so a
    multi-column key never sees this option (a per-column declaration would
    assert more than the key claim and fail on valid data).
    """
    if not keys:
        return Option(
            label="merge on a unique key, checked on every run",
            kind="merge_checked",
            effect=(
                "The same merge, plus dbt's unique test on the chosen column: "
                "declared in a .yml file beside the model, it fails loudly the "
                "first time two rows share a value."
            ),
            columns_prompt="the column(s) that identify a row uniquely",
            declares_test="unique",
            plain=(
                "The same as merging, plus a check that runs alongside: if "
                "two rows ever share the same value in that column, the "
                "check fails the next time it runs and names the value, so "
                "the problem cannot slip past unnoticed."
            ),
        )
    named = ", ".join(keys)
    return Option(
        label=f"merge on {named}, checked on every run",
        kind="merge_checked",
        effect=(
            f"The same merge as 'merge on {named}', plus dbt's unique test on "
            f"{named}: declared in a .yml file beside the model, it fails "
            f"loudly the first time two rows share the same {named}."
        ),
        declares_test="unique",
        plain=(
            f"The same as merging on {named}, plus a check that runs "
            f"alongside: if two rows ever share the same {named}, the check "
            "fails the next time it runs and names the value, so the "
            "problem cannot slip past unnoticed."
        ),
    )


def inline_option() -> Option:
    """The 'inline the literal' answer to a script variable's question."""
    return Option(
        label="inline the literal value",
        kind="inline",
        effect=(
            "The literal from the source SQL is spliced into the model body. The "
            "model stops taking the value at run time and always uses this one."
        ),
        plain=(
            "The value from your script is written straight into the model, so "
            "it is the same on every run and cannot be changed without editing "
            "the file."
        ),
    )


def var_option(name: str = "") -> Option:
    """The 'keep it a dbt var' answer. Names the var when the variable it is
    offered for is known, and stays generic when it is not -- the same
    keys-known/keys-unknown shape `merge_option` uses.
    """
    called = f"var('{name}')" if name else "var() with the variable's own name"
    return Option(
        label="keep as a dbt var",
        kind="var",
        effect=(
            f"The value stays a run-time parameter: the model calls {called} "
            "and dbt supplies it per run, so it can differ between environments."
        ),
        plain=(
            f"The value stays something you set when you run it, so {name or 'it'} "
            "can be different in testing than in production."
        ),
    )


@dataclass(frozen=True, slots=True)
class ModelDescription:
    """What a model is for, in the reader's own words.

    Not an `Answer` and not a `Decision`. Those record a choice among options
    this engine offered and can explain; a description is text only the reader
    can write, because what a model is *for* is not derivable from the SQL
    that builds it. The engine carries it, renders it, and never invents one
    -- a model nobody described has no description, and the .yml says nothing
    rather than guessing.

    `model` is the FINAL name, for the same reason `SchemaTest.model` is: the
    .yml's `models:` entry is resolved by name, and a description recorded
    against a pre-rename draft would describe a model dbt has never heard of.
    """

    model: str
    text: str


@dataclass(frozen=True, slots=True)
class SchemaTest:
    """One dbt test to declare beside a model, because an answer asked for it.

    `model` is the model's FINAL name — the name the .yml's `models:` entry
    carries, and the name the file is written beside. dbt resolves that entry
    by name, so a test recorded against the pre-rename draft name would
    declare a check on a model dbt has never heard of.

    `test` is the dbt test's own name, and it is copied from the `Option`
    that offered it (`Option.declares_test`) rather than spelled at the
    point a test is recorded: nothing can then declare a test that no
    question ever put on the table. It carries the same `Literal` as that
    field, one test wide, because `render_schema_yaml` reasons from exactly
    that to skip merging two entries for one column -- a claim that was
    written before this annotation existed and was not true of `str`.
    """

    model: str
    column: str
    test: Literal["unique"] = "unique"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a Tier-2 question is about, as data rather than as prose.

    `table` is the target's name as the source script spelled it -- the
    spelling a user will recognise, NOT `naming.target_key`'s casefolded
    identity triple, which exists for comparison and would show them a name
    their script does not contain.

    `columns` are the columns the question turns on: the key a MERGE's ON
    clause already names, or empty when the question is precisely which
    column to use. Empty means "none known", never "none exist".

    `candidates` are the columns this model *projects* -- what a picker
    offers when the question is which column to use. It is not another
    spelling of `columns` and the two are never interchangeable: `columns`
    is the key this question turns on, `candidates` is what it could be
    answered with. Every combination occurs, and none of them identifies
    which question this is:

    - both empty -- nothing known about either;
    - `candidates` alone -- an unanswered append: no key named yet, and
      these are the choices;
    - `columns` alone -- a merge, whose key its ON clause named and whose
      projections are not all knowable (a `USING` over a plain table
      renders as `SELECT *`);
    - both -- an append that has been answered, carrying the key the answer
      supplied alongside the projections it was chosen from.

    Ask the options, not this record, whether to render a picker: an option
    carrying a `columns_prompt` is one whose answer needs columns, and it is
    the only reliable signal. Empty means "none known", never "none exist"
    -- a picker handed an empty list must offer free text rather than tell a
    user their model has no columns.

    A consumer needs these to offer a check query, to label a column input,
    to fill a column picker, or to build a worked example. Recovering them
    by parsing `question` would make every consumer a parser of our own
    prose.
    """

    table: str
    columns: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    """One recorded pass action: what was found, what was done, and why."""

    key: str  # unique per decision, e.g. "tier1.build.etl.sql:4"
    tier: Tier
    action: str  # what the pass did or proposes, in dbt terms
    reason: str  # why — dbt-native explanation (seed for teaching copy)
    source_file: str
    line_start: int
    line_end: int
    # The same `reason`, assuming no dbt knowledge. Empty where none has been
    # written yet, and a renderer meeting an empty one shows the dbt-native
    # reason alone rather than inventing a plain one -- the two registers are
    # for two readers, and a consumer that paraphrased is the drift both
    # fields being engine-owned exists to prevent (spec section 11.4(b)).
    # `plain_question` below is the same idea for `question`, and is empty on
    # every Decision that asks none.
    plain_reason: str = ""
    question: str = ""  # Tier-2 only: the design question posed to the user
    plain_question: str = ""  # the same question, assuming no dbt knowledge
    chosen: str = ""  # Tier-2 only: the label of the option that stands
    options: tuple[Option, ...] = ()  # Tier-2 only: every option, chosen included
    subject: Subject | None = None  # Tier-2 questions only: what it is about


def statement_index(decision: Decision) -> int | None:
    """The pipeline statement index embedded in a Decision's key, or None
    when its key carries none.

    `Decision.key`'s documented shape is "<prefix>.<source_file>:<index>"
    (see the example on `key` above) -- every `_decision()` helper across
    tier 1 and tier 2 builds it this way, and the Decisions assemble adds
    for its own actions (`assemble.rename.<name>`) deliberately do not,
    since they answer for a model rather than for a statement. Reading the
    index back out is reading that documented contract, not parsing prose.

    It lives here, beside the field whose shape it knows, because two
    packages now need it: `assemble` matches a Decision to the model that
    inherited its statement, and `emit.example` refuses to illustrate a
    Decision against a model that did not.
    """
    _, _, suffix = decision.key.rpartition(":")
    return int(suffix) if suffix.isdigit() else None


@dataclass(frozen=True, slots=True)
class Answer:
    """A user's reply to one Tier-2 question.

    `label` names the `Option` taken. `columns` carries the extra input an
    option needs when its label alone does not settle it -- "merge on a unique
    key" says nothing about which key.
    """

    label: str
    columns: tuple[str, ...] = ()


def answer_for(decision: Decision, kind: str, columns: tuple[str, ...] = ()) -> Answer:
    """The `Answer` naming this Decision's option of `kind`.

    A consumer records which *kind* of answer the user gave and asks for the
    label at the moment it sends it, because the label is not stable: once an
    answer is applied, the rebuilt Decision spells the merge options with the
    key they named ("merge on order_id"), while the pristine question spells
    them without one. Both are the same answer. Matching on label text made a
    consumer a parser of our own prose and broke on the second answer to any
    model; matching on `kind` is reading a field.

    The Decision handed in is the one the answer will be *sent against*, not
    necessarily the one a screen displayed: every run validates answers
    against the pristine question `run_passes` hands out, so a caller holding
    a rebuilt Decision reads `kind` off it and resolves that kind here against
    the pristine one. That is the whole translation, and it is why this takes
    a Decision rather than an Option.

    Whether the answer carries columns is the *option's* business, not the
    caller's: an option that already names its key has no room for them, and
    `assemble` refuses columns sent to one, because silently discarding what
    a user typed is how a UI comes to disagree with the file it produced.
    `columns_prompt` is the field that says an option needs them, and both
    mismatches are refused here rather than absorbed -- one run's worth of
    columns quietly dropped or quietly supplied is an answer the user did not
    give.
    """
    matched = [option for option in decision.options if option.kind == kind]
    if not matched:
        offered = ", ".join(sorted({option.kind for option in decision.options}))
        raise ValueError(
            f"{decision.key} offers no {kind!r} option "
            f"(it offers {offered or 'no options at all'}); an answer of a kind the "
            "question never asked is a caller bug."
        )
    if len(matched) > 1:
        # One kind is one answer, so this question cannot be answered by kind
        # at all. Returning the first would choose between them by the order
        # they were built in, and hand a user back an answer they did not
        # give -- the same silent pick every other exit here refuses to make.
        # No site builds such a question today; this is what keeps that a
        # property rather than an assumption.
        labels = ", ".join(repr(option.label) for option in matched)
        raise ValueError(
            f"{decision.key} offers {len(matched)} {kind!r} options ({labels}); one "
            "kind is one answer, so a question offering two of a kind cannot be "
            "answered by kind. This is an engine bug, not a caller bug."
        )
    (option,) = matched
    if columns and not option.columns_prompt:
        raise ValueError(
            f"{decision.key}'s {kind!r} option ({option.label!r}) names its own "
            f"key, so it takes no columns; got {columns!r}. Send columns only "
            "for an option carrying a columns_prompt."
        )
    if option.columns_prompt and not columns:
        raise ValueError(
            f"{decision.key}'s {kind!r} option asks {option.columns_prompt!r} and got no columns."
        )
    return Answer(label=option.label, columns=columns)


@dataclass(frozen=True, slots=True)
class ModelDraft:
    """A dbt model in the making. Naming/layout finalized at assemble (slice 4)."""

    name: str  # target table identifier (unqualified), as the script spelled it
    qualified_name: str  # dotted catalog.db.name (non-empty parts only); bare name if unqualified
    # naming.target_key of the target this draft was built from: (catalog, db,
    # name), each casefolded unless it was written quoted. `name` and
    # `qualified_name` are the spellings the script used and are what the
    # report and the model file show; this is what decides whether two drafts
    # are the same table. Comparing the spellings instead makes `EVENTS` and
    # `Events` two models, of which the filesystem keeps one.
    identity: tuple[str, str, str]
    body: str  # the SELECT, regenerated pretty; inner comments preserved
    materialization: str  # "table" | "view"; assemble may omit if layer default
    grants: tuple[tuple[str, tuple[str, ...]], ...]  # (privilege, principals)
    source_indices: tuple[int, ...]  # pipeline indices folded into this draft
    leading_comments: tuple[str, ...]  # statement-level comments, no delimiters
    incremental_strategy: str | None = None  # None means "not incremental"
    unique_key: tuple[str, ...] = ()  # empty means no unique key


@dataclass(frozen=True, slots=True)
class PassState:
    """The pipeline's working set. Passes consume pending items and add output."""

    pending: tuple[tuple[int, ClassifiedStatement], ...]  # (pipeline index, stmt)
    drafts: tuple[ModelDraft, ...]
    decisions: tuple[Decision, ...]
    dialect: str | None
