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
# The four commands, in the order a first run goes: see what would be sent,
# send it, check it, and the one that does both. Names only -- what each does
# is its `Term.plain` below, said once, wherever the glossary is rendered.
#
# Here rather than in either surface because both name them: the web's last
# screen and the report's closing section. Held as `Term.name` spellings and
# checked against the glossary by
# test_the_commands_are_all_glossary_terms, so nothing can name a command
# this tool never defines.
COMMANDS: tuple[str, ...] = ("dbt compile", "dbt run", "dbt test", "dbt build")


GLOSSARY: tuple[Term, ...] = (
    # First, because every other definition here leans on it. The plain
    # register says "model" on almost every screen, and a persona who had
    # never used dbt read it as a machine-learning model. The word is not on
    # the JARGON list -- the plain register needs it -- so it is defined here
    # rather than banned there.
    #
    # Named with a bare common word, which `terms_in` matches as a substring.
    # That is safe for this word: it occurs inside "models" and "modeled",
    # which are the same word, and inside no different word that this tool
    # prints.
    Term(
        name="model",
        plain=(
            "A SELECT query saved in a file of its own in the project. dbt creates a table "
            "or a view in the database from that query. The name of the file, without "
            ".sql, is the name of the model. A model is not a machine learning model "
            "and not a statistical model. One setting tells dbt to create nothing for a "
            "model. Then dbt puts the query inside each model that reads it."
        ),
    ),
    Term(
        name="dbt compile",
        plain=(
            "Creates the actual SQL that each model stands for, and writes that SQL to a "
            "file for you to read. In that SQL, dbt replaces each filled-in name with "
            "the thing that the name stands for. The command sends nothing to the "
            "database, and no table changes. Use this command to see exactly what run "
            "would send, before you send it."
        ),
    ),
    Term(
        name="dbt run",
        plain=(
            "Sends the SQL of each model to the database, and creates or replaces the "
            "table or view that the model describes. This command is the one that "
            "changes what the database stores. The command checks nothing after the "
            "write. The compile command shows you what this command would send. The "
            "build command is this same command, with the checks included."
        ),
    ),
    Term(
        name="dbt test",
        plain=(
            "Evaluates the checks that the .yml files next to your models declare. Then "
            "the command reports which checks failed. You start this command yourself. "
            "The command reads tables that have already been written. The command tells "
            "you that a table contains bad data after that data is in the table. The "
            "command cannot undo the write. The build command does these checks during "
            "the writing, one model at a time."
        ),
    ),
    Term(
        name="dbt build",
        plain=(
            "Does the work of run and test together, one model at a time, in dependency "
            "order. For each model, the command writes the model and then checks it. "
            "Then the command goes to the models that read from that model. If a check "
            "fails, dbt does not change the rest of that chain. The table that dbt "
            "already wrote is still there, exactly as dbt wrote it."
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
            "A setting at the top of a model. The setting tells dbt what to create in "
            "the database for that model. The value `table` means that the database "
            "calculates the rows one time and stores them. The value `view` means that "
            "the database stores only the query. Then the database calculates the rows "
            "again each time something reads them. The setting applies to one model, and "
            "a project can set a default for a whole folder of models."
        ),
    ),
    Term(
        name="incremental",
        plain=(
            "A way to update a table by adding to the rows in it, not by creating the "
            "table again from nothing. The first time, dbt writes the "
            "whole table. After that, dbt writes only the rows that the model's own "
            "filter selects. Each such row goes into the table as a new row, or updates "
            "the row that it matches. This method is quicker on a large table. With "
            "this method, the contents of the table also depend on every earlier "
            "update, not on the last update alone."
        ),
    ),
    Term(
        name="unique_key",
        plain=(
            "The column that dbt uses to match rows when it updates a table in place. "
            "For each row that the model produces, dbt examines the value in that column. "
            "If the table already has that value, the new row replaces the row that has "
            "it. If the table does not have that value, dbt adds the new row. If you "
            "name a column whose values repeat, rows that should stay separate "
            "overwrite one another."
        ),
    ),
    Term(
        name="staging",
        plain=(
            "A layer of models, not a deployment environment. Here, `staging` is the "
            "folder of models that tidy raw tables one for one, so that later models "
            "start from something clean. These models rename columns and correct types, and "
            "they do not use joins. The word has no relation to "
            "staging versus production. The same folder exists in each place where "
            "the project is deployed."
        ),
    ),
    Term(
        name="{{ }}",
        seen_as=("{{",),
        plain=(
            "The double curly brackets in a model file mark a part that dbt replaces "
            "before dbt sends the SQL. The text inside the brackets is not SQL. That "
            "text is an instruction to dbt, and the database receives the result of "
            "the instruction in that place. Everything outside the brackets is "
            "ordinary SQL and reads as SQL."
        ),
    ),
    Term(
        name="Jinja",
        plain=(
            "The templating language that the double curly brackets in a model file "
            "belong to. This language lets a model file contain instructions to dbt "
            "next to its SQL. First, dbt processes these instructions. Then dbt gives "
            "plain SQL to the database. You do not have to write any of this language "
            "to read a converted model. The only parts that this language changes are "
            "the parts inside double brackets."
        ),
    ),
    Term(
        name="ref()",
        seen_as=("ref(",),
        plain=(
            "How a model names another model, in place of the table that the other "
            "model writes. From this call, dbt reads the name of the other model. Then "
            "dbt finds that it must write the other model first, and puts the real "
            "table name into the SQL. If you rename a model, dbt uses the new table name "
            "in place of each of these calls. A table name that you type by hand would "
            "not follow the rename."
        ),
    ),
    Term(
        name="source()",
        seen_as=("source(",),
        plain=(
            "How a model names a table that dbt did not create. Something else loaded "
            "that table into the database. The names come from a .yml file that maps a "
            "short name to a real schema and table. When that table moves, you change "
            "the .yml file, not every model. The idea is the same as naming another "
            "model, but for tables from outside the project."
        ),
    ),
    Term(
        name="config()",
        seen_as=("config(",),
        plain=(
            "The settings at the top of a model file, written as a call and not as SQL. "
            "For example, the settings tell dbt what to create for the model and how to "
            "update the model. Before dbt sends the query, dbt reads these "
            "settings and removes them. So the settings never reach the database."
        ),
    ),
    Term(
        name="warehouse",
        plain=(
            "The database where the SQL actually runs. Examples are Snowflake, "
            "BigQuery, Redshift, Postgres and other databases. dbt stores no data of its "
            "own. dbt sends SQL to the warehouse. Every table that this conversion "
            "mentions is in the warehouse."
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
