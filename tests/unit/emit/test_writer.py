from pathlib import Path

import pytest
import yaml
from tests.unit.assemble.helpers import context_for, convert

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import ProjectContext, SourceInfo, read_project
from dbtw.core.emit import DuplicateSourceEntryError, OrphanSchemaTestError, emit
from dbtw.core.passes import Answer, SchemaTest, verify_option

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _change(sources=(), tests=()) -> ProjectChange:
    return ProjectChange(
        models=(
            AssembledModel(
                name="stg_orders",
                path="models/staging/stg_orders.sql",
                body="SELECT 1 AS a",
                materialization="table",
                grants=(),
                layer="staging",
                depends_on=(),
                leading_comments=(),
                source_indices=(0,),
            ),
        ),
        sources=sources,
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
        tests=tests,
    )


def test_writes_model_report_and_creates_directories(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    written = emit(_change(), ctx, tmp_path)
    model_file = tmp_path / "models" / "staging" / "stg_orders.sql"
    assert model_file.is_file()
    assert "materialized='table'" in model_file.read_text()
    assert (tmp_path / "CONVERSION_REPORT.md").is_file()
    assert model_file in written.paths and (tmp_path / "CONVERSION_REPORT.md") in written.paths


def test_sources_go_beside_the_projects_existing_source_file(tmp_path):
    """with_sources declares sources at models/staging/sources.yml, so ours land
    in that directory — under our own filename, since the project's own file
    already holds that name."""
    ctx = read_project(FIXTURES / "with_sources")
    # A table the project does not already declare: assemble._source_entries
    # never proposes one it does, and _sources_placement asserts that.
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="events"),)),
        ctx,
        tmp_path,
    )
    sources_file = tmp_path / "models" / "staging" / "sources_dbtw.yml"
    assert sources_file.is_file()
    assert "name: raw" in sources_file.read_text()


def test_sources_fall_back_to_the_staging_layer_when_the_project_declares_none(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")  # declares no sources at all
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "staging" / "sources.yml").is_file()


def test_no_sources_file_when_there_are_no_sources(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(_change(), ctx, tmp_path)
    assert not (tmp_path / "models" / "staging" / "sources.yml").exists()


def test_nothing_is_written_outside_out_dir(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    written = emit(_change(), ctx, tmp_path)
    assert all(tmp_path in p.parents for p in written.paths)


def test_emit_refuses_to_write_outside_out_dir(tmp_path):
    """pathlib silently drops the left operand when the right is absolute
    (Path('/tmp/out') / '/etc/passwd' -> '/etc/passwd'). The escape target
    here is a sibling of out_dir inside the test's own tmp tree, not a real
    system path — same vulnerability class (an absolute path escaping
    out_dir), without any risk of this test ever touching a file outside
    its own sandbox, even while it's RED.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    out_dir = tmp_path / "out"
    escape_target = tmp_path / "escaped.sql"  # sibling of out_dir: outside it
    escaping_model = AssembledModel(
        name="evil",
        path=str(escape_target),
        body="select 1",
        materialization=None,
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=(0,),
    )
    change = ProjectChange(
        models=(escaping_model,),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )
    with pytest.raises(ValueError):
        emit(change, ctx, out_dir)
    assert not escape_target.exists()
    assert not out_dir.exists() or not list(out_dir.rglob("*"))


def test_emit_creates_out_dir_when_it_does_not_exist(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    out_dir = tmp_path / "does_not_exist_yet"
    empty_change = ProjectChange(
        models=(),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )
    written = emit(empty_change, ctx, out_dir)
    report_file = out_dir / "CONVERSION_REPORT.md"
    assert report_file.is_file()
    assert written.paths == (report_file,)
    assert written.decisions == ()


def test_sources_at_project_root_land_at_the_root_not_in_staging(tmp_path):
    """sources_at_root declares sources at models/sources.yml (the model-path
    root), mirroring the real dbt-labs jaffle_shop shape, and also has a
    staging layer at models/staging. with_sources' existing source file
    happens to already sit inside its staging layer, so it can't tell rule
    (1) — beside the project's existing source file — apart from rule (2) —
    the staging layer's path. This fixture can.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    staging = tmp_path / "models" / "staging"
    assert not (staging / "sources.yml").exists()
    assert not (staging / "sources_dbtw.yml").exists()


def _context_declaring(*declarations: tuple[str, str, str]) -> ProjectContext:
    """A minimal target project whose only interesting feature is where it
    declares sources. Hand-built rather than read off a fixture because the
    case it exists for — a project that declares sources in a file *not*
    named sources.yml — has no fixture, and the collision it causes is a
    property of ProjectContext alone.
    """
    return ProjectContext(
        project_name="handbuilt",
        model_paths=("models",),
        layers=(),
        existing_models=(),
        existing_sources=tuple(
            SourceInfo(source_name=name, table=table, declared_in=where)
            for name, table, where in declarations
        ),
        vars_declared=(),
        detections=(),
    )


def test_our_sources_file_never_lands_on_the_projects_own(tmp_path):
    """sources_at_root declares raw.orders at models/sources.yml, which is
    where emit would otherwise put ours. Nothing this tool writes may destroy
    a declaration, so ours goes under its own name and out_dir carries no file
    at the project's path at all — copy the whole thing across and raw.orders
    survives.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    assert not (tmp_path / "models" / "sources.yml").exists()
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/sources.yml" in report
    assert "models/sources_dbtw.yml" in report
    assert "raw.orders" in report


def test_the_placement_decision_says_both_files_stand(tmp_path):
    """dbt's duplicate check is per (source, table), not per source name: two
    files declaring source raw with different tables parse clean. The Decision
    has to say that, and offer merging as a preference — claiming dbt rejects
    the pair would send a reader hand-editing YAML to fix nothing.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "dbt reads both" in report
    assert "if you would rather keep one file" in report
    assert "declared twice" not in report
    assert "dbt rejects" not in report


def test_no_collision_is_claimed_when_the_project_declares_no_sources(tmp_path):
    """jaffle_shop declares no sources at all, so ours lands on nothing. A
    Decision here would be an invented clash.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "sources_dbtw" not in report
    assert (tmp_path / "models" / "staging" / "sources.yml").is_file()


def test_no_collision_is_claimed_when_no_sources_file_is_written(tmp_path):
    """sources_at_root does declare sources, but this change proposes none —
    no sources.yml is written, so nothing can land on anything.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(_change(), ctx, tmp_path)
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "sources_dbtw" not in report


def test_a_source_name_declared_in_another_file_is_not_a_collision(tmp_path):
    """The project declares source raw in models/schema.yml, so our file lands
    beside it under the ordinary name. Two files declaring source raw with
    different tables is what dbt accepts, and `_source_entries` has already
    skipped every table the project declares — so there is nothing here to
    report, and a Decision would be a warning about a non-problem.
    """
    ctx = _context_declaring(("raw", "orders", "models/schema.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources.yml").is_file()
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/schema.yml" not in report
    assert "sources_dbtw" not in report


def test_every_declaration_the_projects_file_holds_is_named(tmp_path):
    """with_sources declares raw.customers and raw.orders in the file ours
    would have landed on. A reader deciding whether to merge the two files by
    hand needs both, not one of them.
    """
    ctx = read_project(FIXTURES / "with_sources")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="events"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "raw.customers and raw.orders" in report


def test_the_landing_name_turns_on_the_path_not_on_the_source_names(tmp_path):
    """The project declares source `legacy` in the file ours would land on,
    and we declare `raw` — no source name in common at all. The file is still
    the project's, so ours still goes under its own name: what must not be
    replaced is the file, whatever it happens to declare.
    """
    ctx = _context_declaring(("legacy", "orders", "models/sources.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    assert not (tmp_path / "models" / "sources.yml").exists()
    assert "legacy.orders" in (tmp_path / "CONVERSION_REPORT.md").read_text()


def test_emit_returns_the_placement_decision_it_recorded(tmp_path):
    """The terminal has to be able to say a Decision exists before the user
    runs `cp -r`, and it has to say it in the words the report uses. Returning
    the record itself is what keeps the two from drifting into two different
    explanations of one choice.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    result = emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    (decision,) = result.decisions
    assert "models/sources_dbtw.yml" in decision.action
    assert "models/sources.yml" in decision.action
    assert decision.action in (tmp_path / "CONVERSION_REPORT.md").read_text()


def test_the_landing_name_bumps_past_a_sources_dbtw_the_project_already_has(tmp_path):
    """A previous run's output, copied into the project, leaves a
    sources_dbtw.yml declaring sources of its own. Landing on that is the same
    replacement as landing on sources.yml, one name along — so the name bumps
    until it is one the project does not declare in.
    """
    project = tmp_path / "project"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    (project / "models" / "a.sql").write_text("select 1 as id")
    (project / "models" / "sources.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n"
        "      - name: orders\n      - name: refunds\n"
    )
    (project / "models" / "sources_dbtw.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: shipments\n"
    )
    out = tmp_path / "out"
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        read_project(project),
        out,
    )
    assert (out / "models" / "sources_dbtw_2.yml").is_file()
    assert not (out / "models" / "sources.yml").exists()
    assert not (out / "models" / "sources_dbtw.yml").exists()


def test_emit_refuses_a_source_entry_the_project_already_declares(tmp_path):
    """`assemble._source_entries` skips every (source, table) the project
    declares, so this state cannot come out of the pipeline. If it ever does,
    the placement Decision would tell the user both files stand side by side —
    beside a file that repeats one of the project's tables, which is the one
    duplicate dbt really rejects. An assert would say the same thing and then
    vanish under `python -O`, leaving the self-contradicting Decision written
    to disk; a raise says it in every build.
    """
    ctx = read_project(FIXTURES / "sources_at_root")  # declares raw.orders
    with pytest.raises(DuplicateSourceEntryError, match="raw.orders"):
        emit(
            _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
            ctx,
            tmp_path,
        )
    assert list(tmp_path.rglob("*")) == []  # refused before anything was written


def test_a_models_schema_yml_is_written_beside_it(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    written = emit(_change(tests=(SchemaTest("stg_orders", "order_id"),)), ctx, tmp_path)
    schema_file = tmp_path / "models" / "staging" / "stg_orders.yml"
    assert schema_file.is_file()
    assert "name: order_id" in schema_file.read_text()
    assert schema_file in written.paths


def test_no_schema_yml_when_the_model_has_no_tests(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(_change(), ctx, tmp_path)
    assert not (tmp_path / "models" / "staging" / "stg_orders.yml").exists()


def test_two_tests_for_one_model_produce_one_yml_with_two_columns(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(
        _change(
            tests=(
                SchemaTest("stg_orders", "order_id"),
                SchemaTest("stg_orders", "line_id"),
            )
        ),
        ctx,
        tmp_path,
    )
    schema_file = tmp_path / "models" / "staging" / "stg_orders.yml"
    doc = yaml.safe_load(schema_file.read_text())
    (model_doc,) = doc["models"]
    assert model_doc["name"] == "stg_orders"
    assert {c["name"] for c in model_doc["columns"]} == {"order_id", "line_id"}


def test_a_models_schema_yml_never_lands_on_a_projects_declared_sources_file():
    """A model's schema .yml lands at the model's own final name, one suffix
    removed from its .sql file. For an *existing* project file to sit at that
    path, the model's name would already have to collide with something the
    project has -- which `assemble` reports as its own "collision" Decision
    (assembler.py, `existing_by_name.get`).

    Scope, stated because the name of this test used to promise more than it
    checks: `ProjectContext` carries the project's models and the files it
    declares sources in, not a listing of the project directory, so this
    checks our .yml against the paths ctx does know. A project .yml that ctx
    never sees (a `schema.yml` declaring no sources, say) is invisible to it
    and to emit alike -- the same gap `_sources_placement` has, tracked with
    it. What emit *can* prove is the collision between two files it writes
    itself, which is
    `test_a_models_schema_yml_and_the_sources_file_never_take_one_path`.
    """
    for project in ("with_sources", "sources_at_root"):
        ctx = read_project(FIXTURES / project)
        declared = {s.declared_in for s in ctx.existing_sources}
        assert declared  # only meaningful where the project declares sources
        for model in ctx.existing_models:
            candidate = Path(model.path).with_suffix(".yml").as_posix()
            assert candidate not in declared


def _named_model_change(name: str, path: str, *, sources=(), tests=()) -> ProjectChange:
    return ProjectChange(
        models=(
            AssembledModel(
                name=name,
                path=path,
                body="SELECT 1 AS order_id",
                materialization="table",
                grants=(),
                layer="staging",
                depends_on=(),
                leading_comments=(),
                source_indices=(0,),
            ),
        ),
        sources=sources,
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
        tests=tests,
    )


def test_a_models_schema_yml_and_the_sources_file_never_take_one_path(tmp_path):
    """A model named `sources` puts its schema .yml exactly where the sources
    file wants to land, and emit writes the models first -- so the sources
    write replaced the test file the user chose, silently, while the report
    went on counting the test and listing both paths.

    The sources file moves, not the model's: its name is a constant this
    module picks, while the .yml's is derived from the model's own final name.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")  # declares no sources of its own
    change = _named_model_change(
        "sources",
        "models/staging/sources.sql",
        sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),),
        tests=(SchemaTest("sources", "order_id"),),
    )

    result = emit(change, ctx, tmp_path)

    schema_file = tmp_path / "models" / "staging" / "sources.yml"
    schema_doc = yaml.safe_load(schema_file.read_text())
    assert schema_doc["models"] == [
        {"name": "sources", "columns": [{"name": "order_id", "tests": ["unique"]}]}
    ]

    # The sources file still got written, under a name of its own.
    sources_files = [
        p for p in tmp_path.rglob("*.yml") if "sources" in yaml.safe_load(p.read_text())
    ]
    (sources_file,) = sources_files
    assert sources_file != schema_file
    assert yaml.safe_load(sources_file.read_text())["sources"][0]["tables"] == [{"name": "orders"}]

    assert len(set(result.paths)) == len(result.paths)  # no path reported twice


def test_the_moved_sources_file_says_why_it_moved(tmp_path):
    """Nothing silent: the rename gets a Decision naming the model whose test
    file holds the ordinary name, not the project-already-uses-it reason,
    which would describe a conflict that did not happen here.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = _named_model_change(
        "sources",
        "models/staging/sources.sql",
        sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),),
        tests=(SchemaTest("sources", "order_id"),),
    )

    result = emit(change, ctx, tmp_path)

    (moved,) = [d for d in result.decisions if d.key.startswith("emit.sources_placed")]
    assert "models/staging/sources.yml" in moved.reason
    # The clause naming what holds the ordinary name, specifically -- not just
    # the word "sources", which the action template always contains.
    assert "holds the tests this conversion declares for sources" in moved.action
    # The project declares no sources at all here, so blaming it would describe
    # a conflict that did not happen.
    assert "the target project already uses" not in moved.action
    assert "replace their file" not in moved.reason

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert moved.action in report


def test_the_decision_names_both_holders_when_a_model_and_the_project_share_the_path(tmp_path):
    """`with_sources` declares models/staging/sources.yml, and a model named
    `sources` puts its test file at that same path. Two different things hold
    the ordinary name, for two different reasons -- one would cost the user
    their own declarations on `cp -r`, the other would cost them the test they
    just chose, inside out_dir, before they copied anything. A Decision naming
    only one of them describes half of what happened.
    """
    ctx = read_project(FIXTURES / "with_sources")
    change = _named_model_change(
        "sources",
        "models/staging/sources.sql",
        sources=(SourceEntry(source_name="raw", schema="raw", table="widgets"),),
        tests=(SchemaTest("sources", "order_id"),),
    )

    result = emit(change, ctx, tmp_path)

    (moved,) = [d for d in result.decisions if d.key.startswith("emit.sources_placed")]
    assert "declares sources there" in moved.reason
    assert "tests for sources" in moved.reason

    # And both files survive, under names of their own.
    schema_doc = yaml.safe_load((tmp_path / "models" / "staging" / "sources.yml").read_text())
    assert "models" in schema_doc
    assert (tmp_path / "models" / "staging" / "sources_dbtw.yml").is_file()


def test_a_test_naming_a_model_this_change_does_not_carry_is_refused(tmp_path):
    """Dropping it silently is the failure: the model loop simply never asks
    for it, so no .yml is written -- while the report goes on counting it, and
    says `Tests: 1` beside an out_dir holding none. Nothing upstream can
    produce this today, which is exactly why it is a bug when it appears, and
    the same reason DuplicateSourceEntryError raises rather than asserts.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    with pytest.raises(OrphanSchemaTestError, match="stg_ghost"):
        emit(_change(tests=(SchemaTest("stg_ghost", "order_id"),)), ctx, tmp_path)
    assert list(tmp_path.rglob("*")) == []  # refused before anything was written


def test_no_move_when_the_colliding_model_has_no_test(tmp_path):
    """The .yml only exists for a model with a test, so a model named
    `sources` without one leaves the ordinary name free. Moving anyway would
    rename a file for a conflict that is not there.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = _named_model_change(
        "sources",
        "models/staging/sources.sql",
        sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),),
    )

    result = emit(change, ctx, tmp_path)

    assert (tmp_path / "models" / "staging" / "sources.yml").is_file()
    assert "sources" in yaml.safe_load(
        (tmp_path / "models" / "staging" / "sources.yml").read_text()
    )
    assert not [d for d in result.decisions if d.key.startswith("emit.sources_placed")]


def test_the_alternate_sources_name_is_bumped_past_a_models_schema_yml(tmp_path):
    """A model named `sources_dbtw` takes the alternate name too. The bump
    loop has to count both kinds of taken name or it lands right back on one.
    """
    ctx = read_project(FIXTURES / "with_sources")  # declares models/staging/sources.yml
    change = ProjectChange(
        models=(
            AssembledModel(
                name="sources_dbtw",
                path="models/staging/sources_dbtw.sql",
                body="SELECT 1 AS order_id",
                materialization="table",
                grants=(),
                layer="staging",
                depends_on=(),
                leading_comments=(),
                source_indices=(0,),
            ),
        ),
        sources=(SourceEntry(source_name="raw", schema="raw", table="widgets"),),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
        tests=(SchemaTest("sources_dbtw", "order_id"),),
    )

    emit(change, ctx, tmp_path)

    schema_doc = yaml.safe_load((tmp_path / "models" / "staging" / "sources_dbtw.yml").read_text())
    assert "models" in schema_doc  # ours, not overwritten by the sources file
    assert (tmp_path / "models" / "staging" / "sources_dbtw_2.yml").is_file()


APPEND_SQL = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"


def _question_key(change, table):
    (dec,) = [d for d in change.decisions if d.question and table in d.action]
    return dec.key


def test_end_to_end_the_yml_lands_beside_the_model_and_parses_clean(tmp_path):
    baseline = convert(APPEND_SQL)
    key = _question_key(baseline, "revenue_events")
    change = convert(APPEND_SQL, answers={key: Answer(verify_option().label, ("order_id",))})
    ctx = context_for("jaffle_shop")

    result = emit(change, ctx, tmp_path)

    (model,) = [m for m in change.models if "revenue_events" in m.name]
    model_file = tmp_path / model.path
    schema_file = model_file.with_suffix(".yml")
    assert schema_file.parent == model_file.parent
    assert schema_file.is_file()
    assert schema_file in result.paths

    doc = yaml.safe_load(schema_file.read_text())
    assert doc == {
        "version": 2,
        "models": [{"name": model.name, "columns": [{"name": "order_id", "tests": ["unique"]}]}],
    }

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "**Tests**: 1" in report


COLLIDING_SQL = "INSERT INTO sources SELECT order_id, amount FROM raw.orders;\n"


def test_end_to_end_a_model_named_sources_keeps_the_test_the_user_chose(tmp_path):
    """The whole path, on a project with no layer prefix to rename the model
    out of the way: a table named `sources` stays `sources`, and its test file
    takes `models/sources.yml` -- where the sources file also wants to go.

    The report's count and the tree have to agree. A run that says
    `**Tests**: 1` with no file declaring that test is the report contradicting
    the artifact beside it.
    """
    baseline = convert(COLLIDING_SQL, project="no_conventions")
    key = _question_key(baseline, "sources")
    change = convert(
        COLLIDING_SQL,
        project="no_conventions",
        answers={key: Answer(verify_option().label, ("order_id",))},
    )
    ctx = context_for("no_conventions")

    result = emit(change, ctx, tmp_path)

    schema_doc = yaml.safe_load((tmp_path / "models" / "sources.yml").read_text())
    assert schema_doc == {
        "version": 2,
        "models": [{"name": "sources", "columns": [{"name": "order_id", "tests": ["unique"]}]}],
    }
    assert len(set(result.paths)) == len(result.paths)

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "**Tests**: 1" in report
    assert [p for p in tmp_path.rglob("*.yml") if "sources" in yaml.safe_load(p.read_text())]


def test_end_to_end_an_unanswered_conversion_emits_no_yml_and_reports_zero_tests(tmp_path):
    change = convert(APPEND_SQL)
    ctx = context_for("jaffle_shop")
    emit(change, ctx, tmp_path)

    assert list(tmp_path.rglob("*.yml")) == []
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "**Tests**: 0" in report
