"""Every sentence this tool says to a reader follows the structural rules of
ASD-STE100 (Simplified Technical English).

The reader is a SQL expert who has never used dbt, often reading in a second
language. STE removes the two largest causes of misreading: words with more
than one meaning, and sentences with more than one possible structure. This
test checks the rules that a machine can check without the STE dictionary:

1. A sentence has at most 25 words (STE rules 5.1 and 6.3, descriptive text).
2. No semicolons (rule 8.1). Two sentences in place of one.
3. No contractions (rule 4.2).
4. No Latin abbreviations (GR-6): "for example", not "e.g.".
5. No common phrasal verbs (rule 9.3): "remove", not "take out".

Code, identifiers and SQL stay verbatim (rule 8.6), so a span in backticks
counts as one word. The text is collected from real conversions, with and
without answers, so a sentence the engine builds from parts is checked as
the reader sees it.

`PENDING` lists the text origins not yet rewritten to these rules. Each one
is an expected failure, and strict, so an origin that starts to pass must be
removed from the list. The list is one origin per line on purpose: the
rewrites run in parallel, and each removes only its own line.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbtw.core import intro, teach
from dbtw.core.assemble import CHOOSABLE, AssembledModel, ProjectChange, Variable, assemble
from dbtw.core.context import read_project
from dbtw.core.emit import emit, report
from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import Answer, Decision, SchemaTest, answer_for, run_passes

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROJECTS = FIXTURES / "projects"

PENDING: set[str] = set()

# Scripts that reach the Decisions the fixtures alone do not: a bare query, a
# file with two queries, a MERGE, a read of another project's model, a GRANT,
# a column-list rebuild, an unparseable statement and a script variable.
SCRIPTS = {
    "weekly_revenue.sql": (
        "SELECT customer_id, SUM(amount) AS total FROM raw.orders GROUP BY customer_id"
    ),
    "two_queries.sql": "SELECT a FROM raw.x;\nSELECT b FROM raw.y;\n",
    "dim_customer.sql": (
        "MERGE INTO dim_customer AS t USING raw.customers AS s "
        "ON t.customer_id = s.customer_id "
        "WHEN MATCHED THEN UPDATE SET t.name = s.name "
        "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, s.name);\n"
        "MERGE INTO dim_star AS t USING raw.stars AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *;\n"
    ),
    "nightly.sql": (
        "-- nightly revenue load\n"
        "DECLARE @cutoff DATE = '2024-01-01';\n"
        "INSERT INTO revenue_events SELECT o.order_id, c.name, o.amount, o.order_date "
        "FROM analytics.orders AS o JOIN analytics.dim_customers AS c "
        "ON c.customer_id = o.customer_id WHERE o.order_date >= @cutoff;\n"
        "TRUNCATE TABLE revenue_daily;\n"
        "INSERT INTO revenue_daily (day, total) "
        "SELECT order_date, SUM(amount) FROM revenue_events GROUP BY order_date;\n"
        "GRANT SELECT ON revenue_events TO analyst;\n"
        "THIS IS NOT SQL AT ALL(((;\n"
    ),
}

_CODE = re.compile(r"`[^`]*`")
# A sentence ends at ".", "!" or "?" followed by a space, whatever comes
# next. Requiring a capital letter next joined every sentence that starts
# with "dbt" -- a name written in lower case -- to the sentence before it,
# and writers bent their sentences to avoid starting one with the tool's own
# name. A dotted name ("o.order_id") and a decimal ("1.5") have no space
# after the point, and the Latin abbreviations that would split wrongly are
# refused by this test.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CONTRACTION = re.compile(
    r"\b(?:\w+n't|it's|that's|there's|what's|you're|we're|they're|you've|we've|"
    r"they've|I'm|you'll|we'll|it'll|they'll|let's|isn't|aren't)\b",
    re.IGNORECASE,
)
_LATIN = re.compile(r"\b(?:e\.g|i\.e|etc|viz|cf)\.", re.IGNORECASE)
_PHRASAL = re.compile(
    r"\b(?:take (?:out|off)|set up|carry out|find out|point out|figure out|"
    r"turn (?:on|off)|fill in|look up|pick up|end up|show up|make up|"
    r"come up|go back|give up|hold off|kick off|spin up|wrap up)\b",
    re.IGNORECASE,
)
MAX_WORDS = 25


def sentences(text: str) -> list[str]:
    """The sentences of `text`, with code spans kept whole."""
    spans: list[str] = []

    def keep(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    hidden = _CODE.sub(keep, " ".join(text.split()))
    parts = [p for p in _SENTENCE_END.split(hidden) if p.strip()]
    return [re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], p) for p in parts]


def words(sentence: str) -> int:
    """Words in one sentence. A code span in backticks is one word."""
    return len(_CODE.sub(" CODE ", sentence).split())


def findings(text: str) -> list[str]:
    """Each hard STE rule break in `text`, as a short message."""
    found: list[str] = []
    for sentence in sentences(text):
        n = words(sentence)
        if n > MAX_WORDS:
            found.append(f"{n} words: {sentence}")
    plain = _CODE.sub(" ", text)
    if ";" in plain:
        found.append(f"semicolon: {text}")
    for rule, pattern in (("contraction", _CONTRACTION), ("latin", _LATIN), ("phrasal", _PHRASAL)):
        for match in pattern.finditer(plain):
            found.append(f"{rule} {match.group(0)!r}: {text}")
    return found


def _origin(decision: Decision) -> str:
    return decision.key.split(".", 1)[0]


def _decision_texts(decision: Decision) -> Iterator[str]:
    yield from (decision.reason, decision.plain_reason, decision.question, decision.plain_question)
    for option in decision.options:
        yield from (option.effect, option.plain, option.columns_prompt)


def _answers_for(question: Decision) -> Iterator[Answer]:
    columns = ()
    if question.subject is not None:
        columns = question.subject.columns or question.subject.candidates[:1]
    for option in question.options:
        given = columns if option.columns_prompt else ()
        if option.columns_prompt and not given:
            continue
        yield answer_for(question, option.kind, given)


def _conversions(tmp: Path) -> Iterator[Decision]:
    scripts = tmp / "scripts"
    scripts.mkdir()
    for name, text in SCRIPTS.items():
        (scripts / name).write_text(text, encoding="utf-8")
    sources = [*sorted((FIXTURES / "sql").glob("*.sql")), *sorted(scripts.glob("*.sql"))]
    elsewhere = (read_project(PROJECTS / "core_platform"),)
    for project in (
        "jaffle_shop",
        "three_layers",
        "no_conventions",
        "with_sources",
        "sources_at_root",
    ):
        ctx = read_project(PROJECTS / project)
        for sql in sources:
            ingested = ingest(sql, None)
            state = run_passes(classify_statements(ingested), ingested.dialect)
            pristine = assemble(state, ctx, elsewhere=elsewhere)
            yield from pristine.decisions
            # emit's own Decisions (where the sources file lands, which
            # files a rewrite removed) exist only once something is written.
            out = tmp / "out" / project / sql.stem
            yield from emit(pristine, ctx, out).decisions
            for question in (d for d in pristine.decisions if d.question):
                for answer in _answers_for(question):
                    answered = assemble(
                        state, ctx, answers={question.key: answer}, elsewhere=elsewhere
                    )
                    yield from answered.decisions
            for kind in CHOOSABLE:
                chosen = {m.name: kind for m in pristine.models}
                yield from assemble(
                    state, ctx, materializations=chosen, elsewhere=elsewhere
                ).decisions


def _closing_changes() -> tuple[ProjectChange, ProjectChange]:
    """Two changes for the "Not done yet" section, which is built per change.

    The first has no incremental model, no test, no table reference and no
    variable. The second has two of each, with two references rewritten and
    two left as written, so the plural forms are checked as the reader sees
    them.
    """

    def model(
        name: str, materialization: str | None, body: str = "SELECT 1 AS id"
    ) -> AssembledModel:
        return AssembledModel(
            name=name,
            path=f"models/{name}.sql",
            body=body,
            materialization=materialization,
            grants=(),
            layer="marts",
            depends_on=(),
            leading_comments=(),
            source_indices=(0,),
        )

    def unresolved(table: str) -> Decision:
        return Decision(
            key=f"assemble.rewrite_unresolved.fct_a.{table}",
            tier=2,
            action=f"{table} in fct_a left as written",
            reason="no model or source matches this name",
            source_file="",
            line_start=0,
            line_end=0,
        )

    bare = ProjectChange(
        models=(model("stg_a", None),),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="p",
    )
    full = dataclasses.replace(
        bare,
        models=(
            model("fct_a", "incremental", "SELECT id FROM {{ ref('stg_a') }}"),
            model("fct_b", "incremental", "SELECT id FROM {{ source('raw', 'b') }}"),
        ),
        decisions=(unresolved("legacy.a"), unresolved("legacy.b")),
        tests=(SchemaTest("fct_a", "id"), SchemaTest("fct_b", "id")),
        variables=(Variable("cutoff", None, "etl.sql", 1),),
    )
    return bare, full


def _texts(tmp: Path) -> dict[str, set[str]]:
    by_origin: dict[str, set[str]] = {}

    def add(origin: str, text: str) -> None:
        if text.strip():
            by_origin.setdefault(origin, set()).add(" ".join(text.split()))

    for term in teach.GLOSSARY:
        add("teach", term.plain)
    for text in (intro.HEADLINE, intro.LEDE, intro.PRIVACY):
        add("intro", text)
    for pillar in intro.PILLARS:
        add("intro", pillar.plain)
    for name in dir(report):
        value = getattr(report, name)
        if name.startswith("_") and isinstance(value, str) and len(value.split()) > 6:
            add("report", value)
    for change in _closing_changes():
        add("report", report._render_not_done_yet(change))
    for decision in _conversions(tmp):
        for text in _decision_texts(decision):
            add(_origin(decision), text)
        # Kept apart as well, because a question has one more rule than the
        # rest: the report renders it as "Question: <text>", alone.
        for text in (decision.question, decision.plain_question):
            add(QUESTIONS, text)
    for text in _refusals(tmp):
        add("refusals", text)
    return by_origin


def _refused(call) -> str:  # type: ignore[no-untyped-def]
    """The message of the refusal `call` raises. A call that does not raise
    is a broken trigger, not a passing text, so it fails the collection."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001 -- every refusal type is the point
        return str(exc)
    raise AssertionError(f"{call} did not refuse")


def _refusals(tmp: Path) -> Iterator[str]:
    """Refusals a reader can meet, triggered the way a reader meets them.

    They appear only when something fails, so no conversion above reaches
    them. Each is raised through the public function a front end calls.
    """
    import subprocess

    from dbtw.core.deliver import deliver, push
    from dbtw.core.emit import refuse_output_inside_project
    from dbtw.core.progress import load_progress
    from dbtw.web.state import Source, pasted_file_name

    base = tmp / "refusals"
    base.mkdir()
    jaffle = read_project(PROJECTS / "jaffle_shop")
    script = base / "weekly.sql"
    script.write_text(SCRIPTS["nightly.sql"], encoding="utf-8")
    ingested = ingest(script, None)
    state = run_passes(classify_statements(ingested), ingested.dialect)
    change = assemble(state, jaffle)
    model = change.models[0].name
    question = next(d for d in change.decisions if d.question and d.subject is not None)
    prompted = next(o for o in question.options if o.columns_prompt)

    yield _refused(lambda: read_project(base / "missing"))
    yield _refused(lambda: read_project(base))
    yield _refused(lambda: ingest(script, "no_such_dialect"))
    yield _refused(lambda: refuse_output_inside_project(PROJECTS / "jaffle_shop", jaffle))
    yield _refused(lambda: refuse_output_inside_project(PROJECTS / "jaffle_shop" / "out", jaffle))
    yield _refused(lambda: assemble(state, jaffle, answers={"no.such.key": Answer(label="x")}))
    yield _refused(lambda: answer_for(question, prompted.kind, ()))
    yield _refused(lambda: assemble(state, jaffle, descriptions={model: "{{ var('x') }}"}))
    yield _refused(lambda: assemble(state, jaffle, descriptions={"no_such_model": "text"}))
    yield _refused(lambda: assemble(state, jaffle, materializations={model: "snapshot"}))
    yield _refused(lambda: pasted_file_name("   "))
    yield _refused(lambda: pasted_file_name("../escape"))
    yield _refused(lambda: Source(out=base / "out").start(PROJECTS / "jaffle_shop", {}))

    out = base / "written"
    emit(change, jaffle, out)
    not_a_repo = base / "plain_project"
    not_a_repo.mkdir()
    (not_a_repo / "dbt_project.yml").write_text("name: plain\n", encoding="utf-8")
    yield _refused(lambda: deliver(out, read_project(not_a_repo), branch="b", message="m"))

    repo = base / "repo"
    repo.mkdir()
    (repo / "dbt_project.yml").write_text("name: repo\n", encoding="utf-8")

    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git(
        "-c",
        "user.email=r@example.invalid",
        "-c",
        "user.name=R",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    yield _refused(lambda: push(repo, branch="dbtw/x"))
    (repo / "loose.txt").write_text("uncommitted\n", encoding="utf-8")
    yield _refused(lambda: deliver(out, read_project(repo), branch="b", message="m"))

    damaged = base / "learned.json"
    damaged.write_text("not json", encoding="utf-8")
    unreadable = load_progress(PROJECTS / "jaffle_shop", path=damaged).unreadable
    assert unreadable, "a damaged record must report why"
    yield unreadable


@pytest.fixture(scope="module")
def texts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    return _texts(tmp_path_factory.mktemp("ste"))


# Not an origin: every question and plain question, whatever made it.
QUESTIONS = "questions"


def question_findings(text: str) -> list[str]:
    """How `text` fails to read as one question on its own."""
    found: list[str] = []
    if not text[:1].isupper():
        found.append(f"starts lower-case: {text}")
    if not text.endswith("?"):
        found.append(f"does not end with '?': {text}")
    return found


ORIGINS = ("teach", "intro", "report", "tier1", "tier2", "assemble", "emit", "refusals")


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param(o, marks=pytest.mark.xfail(strict=True, reason="not yet rewritten to STE"))
        if o in PENDING
        else o
        for o in ORIGINS
    ],
)
def test_the_text_follows_the_structural_rules_of_ste(origin: str, texts) -> None:  # type: ignore[no-untyped-def]
    found = [f for text in sorted(texts.get(origin, ())) for f in findings(text)]

    assert not found, f"{len(found)} STE findings in {origin}:\n" + "\n".join(found)


def test_both_forms_of_the_closing_section_are_checked(texts) -> None:  # type: ignore[no-untyped-def]
    """The section is built per change, so no module constant holds it whole.
    Each form must reach the rule test as the reader sees it."""
    checked = " ".join(texts["report"])

    assert "This conversion built no incremental models." in checked
    assert "This conversion found no table reference to rewrite" in checked
    assert "This tool rewrote 2 table references as ref() or source() calls." in checked
    assert "This conversion built 2 incremental models: fct_a, fct_b." in checked
    assert "This tool left 2 table references as written" in checked
    assert "the tests that your answers asked for: 2." in checked


def test_every_origin_has_text_to_check(texts) -> None:  # type: ignore[no-untyped-def]
    """An origin with no text would pass the rule test for nothing."""
    missing = [o for o in ORIGINS if o not in texts]
    assert not missing, f"no text collected for {missing}"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("One. Word " + " ".join(["word"] * 25) + ".", "26 words"),
        ("A first part; a second part.", "semicolon"),
        ("It doesn't run.", "contraction"),
        ("Some layers, e.g. staging.", "latin"),
        ("Take out the filter.", "phrasal"),
    ],
)
def test_the_check_catches_each_rule(text: str, rule: str) -> None:
    assert any(f.startswith(rule) for f in findings(text)), findings(text)


@pytest.mark.parametrize(
    "text",
    [
        " ".join(["word"] * 25) + ".",
        "The model reads `" + " ".join(["x"] * 40) + "` once.",
        "Write `a; b` in the file.",
        "The value is 1.5 and the name is o.order_id.",
        # Two sentences of 20 words, the second starting with the tool's
        # lower-case name. The old splitter read them as one of 41 words.
        " ".join(["word"] * 19) + " end. dbt " + " ".join(["word"] * 19) + ".",
    ],
)
def test_the_check_passes_text_that_keeps_the_rules(text: str) -> None:
    assert findings(text) == []


def test_every_question_is_a_sentence_that_asks(texts) -> None:  # type: ignore[no-untyped-def]
    """A screen shows a question alone, and the report renders it as
    "Question: <text>". So each one starts with a capital and ends with "?".
    A merge question once read "Question: does order_id uniquely identify..."."""
    asked = texts.get(QUESTIONS, set())
    assert len(asked) > 5, "too few questions collected to check anything"
    found = [f for text in sorted(asked) for f in question_findings(text)]

    assert not found, "\n".join(found)


def test_the_question_check_catches_each_rule() -> None:
    assert question_findings("does order_id identify a row?")
    assert question_findings("Does order_id identify a row.")
    assert question_findings("Does order_id identify a row?") == []
