"""Words the tool uses that a first-time reader has no way to look up.

Engine-owned for the same reason `Decision.reason` is: the report and the
screens both render these, so a definition written in a template would be a
second explanation of one word, free to drift from the first. These define
dbt's vocabulary rather than anything about one conversion, which is why they
live here and not on a Decision.

The list is not a guess about what is hard. It is the other side of
`tests/unit/passes/test_plain_register.JARGON` -- the words the persona
walkthroughs recorded as unreadable, which the plain register is therefore
forbidden to use. A word banned from one register and defined in neither is a
word the reader has nowhere to look up, so every entry on that list is defined
here, and `test_every_jargon_word_is_defined_by_exactly_one_term` holds the
two lists against each other.

`terms_in` rather than one rendered block, because the two surfaces do not use
the same words: over a real conversion of `incremental_etl.sql` the report
says `incremental` 18 times, `staging` 16 and the four dbt commands not once
-- those are recommended by the screens. A surface that renders the whole
glossary is asking the reader to scroll past twelve definitions to reach the
two it needed, which is measured not to work: four of five personas never
reached the round-1 block that dumped everything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Term:
    """One word, defined once.

    `seen_as` is the spelling to match in text where the name is not it.
    `{{ }}` is how the brackets are named to a reader and never how they are
    written; `ref()` is written `ref(` in a model file. A term whose name is
    already the spelling leaves it empty -- `materialized` appears as
    `materialized='table'`, so matching the bare word finds it.

    A spelling rather than a pattern per term on purpose: the glossary is
    prose, and a regex column would be a second place to get a word wrong,
    reviewed by whoever was reviewing the wording. What it costs is that no
    term's spelling may nest inside another's, or one occurrence would draw
    two definitions with nothing saying which was meant --
    `test_no_term_s_spelling_is_hidden_inside_another_s` holds that.
    """

    name: str
    plain: str
    seen_as: tuple[str, ...] = ()

    @property
    def spellings(self) -> tuple[str, ...]:
        """Every way this term is written where a reader meets it."""
        return (self.name, *self.seen_as)


# Ordered as a reader meets the ideas rather than alphabetically: the four
# commands, then the words inside a model file. `terms_in` re-orders by first
# appearance anyway, so this order is only what settles a tie.
#
# Every definition is written to the same rule the plain register is held to:
# it may name itself and it may use no other word on the JARGON list. That is
# why `dbt run` relates itself to "build" and not to "dbt build", and why the
# bracket entry describes the brackets in words instead of printing a pair.
GLOSSARY: tuple[Term, ...] = (
    Term(
        name="dbt compile",
        plain=(
            "Works out the SQL each model really stands for -- every filled-in name "
            "replaced by the thing it names -- and writes that SQL to a file for you "
            "to read. Nothing is sent to the database and no table changes: this is "
            "the command for seeing exactly what run would send, before you send it."
        ),
    ),
    Term(
        name="dbt run",
        plain=(
            "Sends each model's SQL to the database and creates or replaces the table "
            "or view that model describes. This is the command that changes what is "
            "stored. It checks nothing afterwards: compile shows you what it would "
            "send, and build is this same command with the checks folded into it."
        ),
    ),
    Term(
        name="dbt test",
        plain=(
            "Evaluates the checks declared in the .yml files beside your models and "
            "reports which ones failed. You invoke it yourself, and it reads tables "
            "that have already been written: it tells you a table contains bad data "
            "after that data is in it, and it cannot undo the write. build is the "
            "command that does this alongside the writing, one model at a time."
        ),
    ),
    Term(
        name="dbt build",
        plain=(
            "Does the work of run and test together, one model at a time and in "
            "dependency order: write a model, check it, then move on to the models "
            "that read from it. If a check fails, dbt leaves the rest of that chain "
            "alone -- and the table it had already written is still there, exactly "
            "as it was written."
        ),
    ),
    Term(
        name="materialized",
        # The other spelling of one word, not a second word: the report's
        # models table heads a column "Materialization" and a model file says
        # `materialized='table'`. Two entries would be two definitions of one
        # idea, free to drift -- the thing this module exists to prevent.
        seen_as=("materialization",),
        plain=(
            "A setting at the top of a model saying what dbt should create in the "
            "database for it. `table` means the rows are worked out once and stored; "
            "`view` means only the query is stored, and the rows are worked out again "
            "every time something reads them. It is set per model, and a project can "
            "set a default for a whole folder of them."
        ),
    ),
    Term(
        name="incremental",
        plain=(
            "A way of bringing a table up to date by adding to what is in it rather "
            "than building it again from nothing. The first time, dbt writes the "
            "whole table; after that it writes only the rows the model's own filter "
            "selects, adding them or updating the rows they match. Quicker on a large "
            "table, and it means the table's contents depend on every earlier update "
            "rather than on the last one alone."
        ),
    ),
    Term(
        name="unique_key",
        plain=(
            "The column dbt matches rows on when it updates a table in place. A row "
            "the model produces whose value in that column is already in the table "
            "replaces the row that has it; a row whose value is not there is added. "
            "Name a column whose values repeat and rows that should have stayed "
            "separate overwrite one another."
        ),
    ),
    Term(
        name="staging",
        plain=(
            "A layer of models, not a deployment environment. `staging` here is the "
            "folder of models that tidy raw tables one for one -- renamed "
            "columns, corrected types, no joins -- so that the models built after "
            "them start from something clean. It has nothing to do with "
            "staging-versus-production: the same folder exists wherever the project "
            "is deployed."
        ),
    ),
    Term(
        name="{{ }}",
        seen_as=("{{",),
        plain=(
            "The double curly brackets in a model file mark something dbt fills in "
            "before the SQL is sent. What sits inside them is not SQL -- it is an "
            "instruction to dbt, and the database receives the result of it in that "
            "place. Everything outside them is ordinary SQL and reads as SQL."
        ),
    ),
    Term(
        name="Jinja",
        plain=(
            "The templating language the double curly brackets in a model file "
            "belong to. It is what lets a model file carry instructions to dbt "
            "beside its SQL; dbt works through them first and hands the database "
            "plain SQL. You do not have to write any of it to read a converted "
            "model -- the only parts it fills in are the ones inside double brackets."
        ),
    ),
    Term(
        name="ref()",
        seen_as=("ref(",),
        plain=(
            "How a model names another model instead of naming the table that model "
            "writes. dbt reads the name out of it, works out that the model named has "
            "to be written first, and puts the real table name into the SQL. Rename a "
            "model and every one of these follows it; a table name typed in by hand "
            "would not."
        ),
    ),
    Term(
        name="source()",
        seen_as=("source(",),
        plain=(
            "How a model names a table dbt did not create -- one that something else "
            "loaded into the database. The names come from a .yml file mapping a "
            "short name onto a real schema and table, so when that table moves you "
            "change the .yml file rather than every model. The same idea as naming "
            "another model, for tables from outside the project."
        ),
    ),
    Term(
        name="config()",
        seen_as=("config(",),
        plain=(
            "The settings at the top of a model file, written as a call rather than "
            "as SQL: what dbt should create for the model, how it should be brought "
            "up to date, and so on. dbt reads them and takes them out before the "
            "query is sent, so they never reach the database."
        ),
    ),
    Term(
        name="warehouse",
        plain=(
            "The database the SQL actually runs in -- Snowflake, BigQuery, Redshift, "
            "Postgres and so on. dbt stores no data of its own: it sends SQL to the "
            "warehouse, and every table this conversion talks about lives there."
        ),
    ),
)


def terms_in(text: str) -> tuple[Term, ...]:
    """The glossary terms `text` actually uses, in the order they first
    appear in it.

    A screen calls this to define a word where the reader meets it. Dumping
    the whole glossary on one screen was what the walkthroughs found does not
    work -- four of five never scrolled to it.

    Matching is case-insensitive, because a word is the same word wherever it
    falls in a sentence: the report writes "Incremental models" at the start
    of one and "dbt Jinja" inside another. Each term is returned once however
    many times it occurs, and each is matched independently of the others,
    which is sound only because no term's spelling nests inside another's.

    Plain substrings, with no word boundary: `materialized` has to be found
    inside `materialized='table'` and `ref(` inside a rewritten body, and a
    boundary would lose both. What that costs is that a term named with a
    bare common word would be found inside longer ones -- a `run` inside
    "rerun", a `test` inside "latest". So it is safe because of how the terms
    are *named*, not because of anything this function does: every one of
    them carries a prefix (`dbt run`) or punctuation (`ref(`, `{{`) that does
    not recur inside ordinary words. `terms_in` cannot enforce that, so
    test_no_term_is_named_a_word_that_occurs_inside_other_words does.
    """
    haystack = text.lower()
    found: list[tuple[int, int, Term]] = []
    for order, term in enumerate(GLOSSARY):
        at = [i for i in (haystack.find(s.lower()) for s in term.spellings) if i >= 0]
        if at:
            # `order` breaks a tie between two terms found at the same offset,
            # which one spelling starting inside another would produce. It
            # cannot arise in this glossary and is here so that the ordering
            # is total rather than dependent on sort stability.
            found.append((min(at), order, term))
    found.sort(key=lambda entry: (entry[0], entry[1]))
    return tuple(term for _, _, term in found)
