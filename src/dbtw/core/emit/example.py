"""A worked example for a Tier-2 question: the same input, under the script
and under the model dbt will build.

Values are placeholders, never data. This engine does not read the warehouse
and cannot know a single real row -- an example showing plausible values
would be inventing the user's table. Column names, by contrast, are real:
they are the ones the model's own body projects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dbtw.core.assemble.projections import known_projections
from dbtw.core.assemble.types import AssembledModel
from dbtw.core.passes.types import Decision

Row = tuple[str, ...]

# Every `{{ ... }}` in an assembled body, which by then is dbt Jinja rather
# than SQL. `.` must match newlines: `rewrite_body` never wraps a tag, but a
# body is not guaranteed to have come from it.
_JINJA_TAG = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# What each tag is swapped for so the body parses. Deliberately not a name a
# SELECT list could plausibly contain: seeing it as an output name is how this
# module detects that a tag stood where a column name should be.
_STAND_IN = "_dbtw_jinja_stand_in"


@dataclass(frozen=True, slots=True)
class Example:
    """One input, rendered three ways: what is in the table already, what the
    script leaves behind, and what the model leaves behind."""

    columns: tuple[str, ...]
    key: str  # the column the model matches on; "" when it matches on nothing
    before: tuple[Row, ...]
    script_after: tuple[Row, ...]
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
    returns it.

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


def _placeholder_row(columns: tuple[str, ...], suffix: str = "") -> Row:
    return tuple(f"<{name}{suffix}>" for name in columns)


def worked_example(
    decision: Decision, model: AssembledModel, dialect: str | None
) -> Example | None:
    """The example for `decision` as it applies to `model`, or None when one
    cannot be built honestly.

    None is returned when the decision asks no question, when it carries no
    subject, or when the model's projected columns are not fully known --
    because the body would not parse, because it projects a star, or because
    one of its projections has no output name (a bare `CASE`). In each of
    those the column list genuinely is not known at convert time, and
    guessing it is the one thing this module exists not to do. Rendering the
    columns that *are* known would be no better: an example listing two of a
    row's three columns describes a table the user does not have, which is
    the same lie as inventing a value, told by omission.
    """
    if not decision.question or decision.subject is None:
        return None

    known = _parseable_projections(model.body, dialect)
    if known is None:
        return None
    projections, has_star, has_unnamed = known
    if has_star or has_unnamed or not projections:
        return None

    columns = tuple(name for name, _quoted in projections)
    existing = _placeholder_row(columns)
    arriving = _placeholder_row(columns, suffix="-new")

    if model.incremental_strategy == "merge" and model.unique_key:
        # The row already present is updated in place; the new one is added.
        return Example(
            columns=columns,
            key=", ".join(model.unique_key),
            before=(existing,),
            script_after=(existing,),
            model_after=(existing, arriving),
        )

    # Append, or an unanswered question that currently appends: the existing
    # row stays and everything the SELECT returns is added again.
    return Example(
        columns=columns,
        key="",
        before=(existing,),
        script_after=(existing,),
        model_after=(existing, existing, arriving),
    )
