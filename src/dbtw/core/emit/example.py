"""A worked example for a Tier-2 question: one row, shown as it sits in the
table now and as the model dbt will build leaves it after a run.

Values are placeholders, never data. This engine does not read the warehouse
and cannot know a single real row -- an example showing plausible values
would be inventing the user's table. Column names, by contrast, are real:
they are the ones the model's own body projects.

There is deliberately no "and here is what your script did" side. The script's
behaviour is not derivable from the two things this function is given: an
`AssembledModel` carries the model's body and strategy, not the statement it
came from, so an INSERT with its own `WHERE` filter and a MERGE with no
`WHEN NOT MATCHED` branch would both be described wrongly, and confidently.
What the script did is recorded elsewhere, as Decisions, by passes that read
the statement. This module shows only what it can see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dbtw.core.assemble.types import AssembledModel
from dbtw.core.naming import same_identifier
from dbtw.core.passes.types import Decision, statement_index
from dbtw.core.projections import known_projections

Row = tuple[str, ...]

# Every `{{ ... }}` in an assembled body, which by then is dbt Jinja rather
# than SQL. `.` must match newlines: `rewrite_body` never wraps a tag, but a
# body is not guaranteed to have come from it.
_JINJA_TAG = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# What each tag is swapped for so the body parses. Deliberately not a name a
# SELECT list could plausibly contain: seeing it as an output name is how this
# module detects that a tag stood where a column name should be.
_STAND_IN = "_dbtw_jinja_stand_in"

# What an output column name cannot carry into a rendered example. A quoted
# alias may legally contain a line break (`SELECT amount AS "a<newline>b"` is
# a real Postgres column), and a line break ends a row wherever it falls: the
# table stops mid-cell and every cell and row after it is lost. Nothing
# escapes it back -- unlike a pipe, which a renderer escapes, or a backtick,
# which it fences around; `emit.report` does both.
#
# So the refusal is here rather than in a renderer, for the reason every
# other refusal in this module is: a consumer holding an Example is entitled
# to assume it can be rendered, and a second consumer would otherwise have to
# rediscover this on its own page. The cost is stated rather than hidden: a
# consumer that *could* show it -- an HTML cell holds a newline happily --
# loses the example for a column named this way. Taken deliberately, because
# no example is never wrong and a table that stops mid-row always is.
_LINE_BREAKS = ("\n", "\r")


# The claim every Example is rendered under, owned here rather than by a
# renderer. It is not a caption a consumer invents for its own layout
# ("Worked example.", the heading `emit.report` puts this after, is exactly
# that); it is an assertion about where these values came from, and the
# only thing that makes it true lives in this module: `worked_example` is
# handed a Decision and an AssembledModel, and neither of them contains a
# row. A consumer cannot vouch for that, so a consumer must not be the one
# saying it -- a second one would reinvent the wording with no guarantee it
# says the same thing, which is how `Option.effect` came to be produced at
# five sites and read at none, told from the other end.
#
# Worded without reference to where it sits on a page, so a renderer that
# puts it beside or after its rows rather than above them can still use it
# verbatim.
PLACEHOLDER_NOTICE = "The values are placeholders -- this conversion has not read your data."

# The labels an Example's two row blocks carry, owned here for the reason the
# notice above them is. They read like captions; they are not. Each states an
# invariant this module exists to hold, and a renderer cannot vouch for
# either.
#
# "suppose" is doing the work in the first: `before` is a premise the reader
# is asked to grant, not a row this engine read -- it reads no rows at all.
# "in your table" would claim knowledge the conversion does not have.
#
# The second names *this model* as what acts. The model's own strategy and
# unique_key decide the rows under it, and `worked_example` is handed a model
# and a Decision, never the statement -- so it holds no evidence about the
# reader's script and nothing here may be labelled as what that script does.
# That is precisely the block deleted from this branch two rounds ago, when
# "and here is what your script did" rendered a false comparison against this
# project's own fixture; a second consumer authoring "after your script runs"
# for its own page would write it straight back. A string carrying the
# invariant is not something each renderer may reinvent.
#
# Worded without reference to where they sit, like the notice, so a renderer
# that lays its rows out some other way can still use them verbatim.
SUPPOSED_ROW_LABEL = "suppose this row is already there"
AFTER_RUN_LABEL = "after this model runs"


@dataclass(frozen=True, slots=True)
class Example:
    """One row, rendered two ways.

    `before` is the premise: suppose the model's table already holds this.
    `model_after` is what dbt leaves there once the model runs. Both are
    placeholder rows over `columns`, which are the model's real output column
    names. A renderer must label them as those two things and must not label
    either as anything about the user's script -- see the module docstring.

    `key` names the column(s) a merge matches on, joined by ", ", and is ""
    for a model that matches on nothing.
    """

    columns: tuple[str, ...]
    key: str
    before: tuple[Row, ...]
    model_after: tuple[Row, ...]


def _parseable_projections(
    body: str, dialect: str | None
) -> tuple[list[tuple[str, bool]], bool, bool] | None:
    """`known_projections` for a body that has already been rewritten into
    dbt Jinja, which is every body an `AssembledModel` carries.

    `known_projections` reads SQL, and `{{ source('raw', 'events') }}` is not
    SQL -- it fails to tokenise, so an assembled body read directly always
    comes back None. That None says nothing about the query; it says the text
    was Jinja. Folding the two together would make every model look
    unreadable and every example vanish, and would make the star refusal
    below look like a judgement about `SELECT *` when it was really just a
    parse failure.

    So each tag is swapped for a stand-in identifier, which parses wherever a
    tag can legally stand. A tag in the FROM clause becomes a table name this
    module never looks at. A tag in the SELECT list is the case that matters:
    an unaliased `{{ var('cutoff') }}` compiles to a literal whose output name
    the warehouse settles, not this engine, and the stand-in is emphatically
    not it -- so a stand-in surfacing as an output name means the column list
    is unknown, and None is returned for the same reason the star case
    returns it. An *aliased* tag (`{{ var('cutoff') }} AS cutoff`) is the one
    case where a tag legitimately contributes a name, and it is kept: the
    alias is written in the body and owes nothing to run time.

    A Jinja *block* (`{% ... %}`) is left alone and the body simply does not
    parse. That is the honest answer: a block can add or remove projections
    depending on a run-time condition, so its column list is not knowable
    here either.
    """
    neutralised = _JINJA_TAG.sub(_STAND_IN, body)
    known = known_projections(neutralised, dialect)
    if known is None:
        return None
    projections, has_star, has_unnamed = known
    if any(name == _STAND_IN for name, _quoted in projections):
        return None
    return projections, has_star, has_unnamed


def _placeholder_row(
    projections: list[tuple[str, bool]], key_columns: tuple[str, ...], *, changed: bool
) -> Row:
    """A row of `<column>` placeholders over the model's projections.

    `changed` swaps in `<column-new>` for every column that is not one of
    `key_columns`, which is what makes a merge's update visible: the key
    holds its value -- a row whose key changed is a different row, not an
    updated one -- while everything else moves. With `key_columns` empty and
    `changed` set, every cell moves, which is a row that matches nothing and
    so is inserted rather than updated.

    Takes the projections rather than their bare names because "is this
    column one of the keys" is `same_identifier`'s question and it needs to
    know whether the output name was written quoted.
    `_every_key_is_projected` asks the same question from the other end --
    does any column match this key -- and the two have to answer it the same
    way. Folded case here and `same_identifier` there disagree on exactly the
    row that matters: `SELECT order_id, x AS "ORDER_ID"` keyed on order_id
    clears the guard on the unquoted column, and then a casefold would hold
    the quoted one fixed as well, showing a column unchanged that dbt's merge
    overwrites on every dialect that respects quoting.
    """
    return tuple(
        f"<{name}-new>"
        if changed and not any(same_identifier(key, False, name, quoted) for key in key_columns)
        else f"<{name}>"
        for name, quoted in projections
    )


def _every_key_is_projected(
    unique_key: tuple[str, ...], projections: list[tuple[str, bool]]
) -> bool:
    """Whether every key column is one of the model's output columns.

    `same_identifier` folds case unless a side was written quoted, matching
    `assembler._key_status`; a quoted output name differing only by case is
    not a confident match and is treated here as no match at all. A key that
    is not an output column cannot be held fixed in `_placeholder_row`, so
    the "updated" row would come back identical to the "inserted" one and the
    example would show a merge updating nothing.
    """
    return all(
        any(same_identifier(key, False, name, quoted) for name, quoted in projections)
        for key in unique_key
    )


def worked_example(
    decision: Decision, model: AssembledModel, dialect: str | None
) -> Example | None:
    """The example for `decision` as it applies to `model`, or None when one
    cannot be built honestly.

    None is returned when:

    * the decision asks no question, or carries no subject -- there is
      nothing to illustrate and nothing for it to be about;
    * the decision did not come from a statement this model was built from.
      Statement index against `AssembledModel.source_indices` is how
      `assembler._find_incremental_decision_index` pairs the two, and for the
      reason given there: a name collision gives two statements one model
      name, so comparing names cannot tell their Decisions apart. A caller
      that pairs them wrongly gets nothing rather than a confident example of
      the wrong table;
    * the model's projected columns are not fully known -- the body would not
      parse, it projects a star, or a projection has no output name. Then the
      column list genuinely is not known at convert time, and guessing it is
      the one thing this module exists not to do. Rendering the columns that
      *are* known would be no better: an example listing two of a row's three
      columns describes a table the user does not have, which is the same lie
      as inventing a value, told by omission;
    * a column's name contains a line break, which no row-shaped rendering can
      carry and no escape brings back -- see `_LINE_BREAKS`;
    * the model is not incremental, or is a merge with no unique key, or is a
      merge whose key it does not select. Each of those is a model whose
      run-time behaviour is not the one the branches below draw, and drawing
      it anyway would put a confident picture under a contradicting config.
    """
    if not decision.question or decision.subject is None:
        return None

    index = statement_index(decision)
    if index is None or index not in model.source_indices:
        return None

    known = _parseable_projections(model.body, dialect)
    if known is None:
        return None
    projections, has_star, has_unnamed = known
    if has_star or has_unnamed or not projections:
        return None

    columns = tuple(name for name, _quoted in projections)
    if any(char in name for name in columns for char in _LINE_BREAKS):
        return None

    existing = _placeholder_row(projections, (), changed=False)
    # A row whose key matches nothing already there, so every cell is new.
    inserted = _placeholder_row(projections, (), changed=True)

    if model.incremental_strategy == "merge":
        if not model.unique_key or not _every_key_is_projected(model.unique_key, projections):
            return None
        # The row already there is found by its key and updated in place --
        # the key column holds, the rest move -- and the row matching nothing
        # is added. One row in, two rows out, neither of them a duplicate.
        updated = _placeholder_row(projections, model.unique_key, changed=True)
        return Example(
            columns=columns,
            key=", ".join(model.unique_key),
            before=(existing,),
            model_after=(updated, inserted),
        )

    if model.incremental_strategy == "append":
        # Nothing is matched against anything, so the row already there is
        # left alone and re-inserted beside itself: the same values twice.
        return Example(
            columns=columns,
            key="",
            before=(existing,),
            model_after=(existing, existing, inserted),
        )

    return None
