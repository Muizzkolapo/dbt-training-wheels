"""The report shows one model file, as dbt will read it.

Found by scoring the persona's comprehension check against ten real
conversions. After the report learned to name the dbt commands, a reader of
the CLI had met 12 of the glossary's 14 words. The two left were `{{ }}` and
`config()` -- syntax, met the moment a model file is opened, and the report
listed model paths and never a model's contents. The walk shows a body on its
files screen; the report did not, so the CLI reader could not meet either.

The comprehension check hands the reader a model file and asks what it builds
and what it reads. A report that never shows one is asking them to answer
that about a thing they have not seen.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.assemble import AssembledModel
from dbtw.core.context import read_project
from dbtw.core.emit import render_model
from dbtw.core.emit.report import MODEL_HEADING, render_report

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"
GLOSSARY_HEADING = "## Words this report uses"


def _model(name: str, body: str, **kw: object) -> AssembledModel:
    base: dict[str, object] = dict(
        name=name,
        path=f"models/staging/{name}.sql",
        body=body,
        materialization=None,
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=(0,),
    )
    base.update(kw)
    return AssembledModel(**base)  # type: ignore[arg-type]


PLAIN = _model("stg_plain", "SELECT 1 AS a")
SOURCED = _model("stg_sourced", "SELECT s FROM {{ source('raw', 'orders') }}")
CONFIGURED = _model(
    "stg_configured",
    "SELECT a FROM {{ source('raw', 'orders') }}",
    materialization="table",
)


def _report(*models: AssembledModel) -> str:
    return render_report(_change(models=models), read_project(FIXTURES / "jaffle_shop"))


def _section(report: str) -> str:
    return report.split(MODEL_HEADING)[1].split("\n## ")[0]


def test_the_report_shows_a_model_file_exactly_as_it_was_written() -> None:
    section = _section(_report(CONFIGURED))

    assert f"```sql\n{render_model(CONFIGURED)}```" in section


def test_the_section_names_the_path_the_file_was_written_to() -> None:
    section = _section(_report(CONFIGURED))

    assert "`models/staging/stg_configured.sql`" in section


def test_showing_a_configured_model_is_what_makes_the_glossary_define_the_syntax() -> None:
    """The glossary is read off the report, so syntax the report never showed
    was a word it never defined. This is the whole reason for the section.
    """
    report = _report(CONFIGURED)
    glossary = report.split(GLOSSARY_HEADING)[1].split("\n## ")[0]

    assert "- **{{ }}** —" in glossary
    assert "- **config()** —" in glossary


def test_the_model_shown_is_the_one_that_shows_the_most_dbt_words() -> None:
    """Listed first and last, so neither position wins by being a position."""
    for models in ((PLAIN, CONFIGURED, SOURCED), (SOURCED, PLAIN, CONFIGURED)):
        section = _section(_report(*models))

        assert render_model(CONFIGURED) in section
        assert "stg_sourced" not in section
        assert render_model(SOURCED) not in section
        assert render_model(PLAIN) not in section


def test_words_the_rest_of_the_report_already_says_do_not_count() -> None:
    """`ref(` and `source(` are in "Not done yet" in every report, so a model
    full of them teaches nothing the report does not already define. Counted,
    the ref/source model ties with the config model below, wins the tie by
    position, and `config()` is left undefined -- the gap this section closes.
    """
    joined = _model(
        "stg_joined",
        "SELECT j FROM {{ ref('stg_x') }} JOIN {{ source('raw', 'orders') }} USING (j)",
    )
    configured = _model("stg_tabled", "SELECT 1 AS t", materialization="table")

    report = _report(joined, configured)
    glossary = report.split(GLOSSARY_HEADING)[1].split("\n## ")[0]

    assert render_model(configured) in _section(report)
    assert "- **config()** —" in glossary


def test_a_fence_inside_the_model_does_not_close_the_block() -> None:
    fenced = _model("stg_fenced", "SELECT '\n```\n' AS odd")

    section = _section(_report(fenced))

    assert f"````sql\n{render_model(fenced)}````" in section


def test_a_tie_goes_to_the_first_model_in_dependency_order() -> None:
    other = _model("stg_other", "SELECT o FROM {{ source('raw', 'items') }}")

    section = _section(_report(SOURCED, other))

    assert render_model(SOURCED) in section
    assert render_model(other) not in section


def test_one_model_is_shown_however_many_were_written() -> None:
    section = _section(_report(PLAIN, SOURCED, CONFIGURED))

    assert section.count("```sql") == 1


def test_a_model_with_no_dbt_syntax_is_still_shown() -> None:
    """A plain SELECT in a file is lesson 1 -- a model is a SELECT in a file.
    Showing it is the lesson, even with nothing for dbt to fill in.
    """
    section = _section(_report(PLAIN))

    assert render_model(PLAIN) in section


def test_no_models_means_no_section() -> None:
    assert MODEL_HEADING not in _report()


def test_the_section_follows_the_models_table() -> None:
    report = _report(CONFIGURED)

    assert report.index("## Models") < report.index(MODEL_HEADING) < report.index("## Sources")
