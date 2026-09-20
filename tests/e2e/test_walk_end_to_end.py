"""The whole product, once, with nothing stubbed.

Every other test here holds one piece to its contract. This one is the only
test that starts where a reader starts -- a browser, an empty app, no project
and no SQL -- and finishes where they finish: a commit on a branch of their
own repository, holding files whose bytes this test reads off disk.

It exists because every piece of this walk has passed its own tests while the
product was broken between them. The session had a describe screen before
anything carried a description into a .yml; the what-changed screen paired
models to SQL for a week before anything asserted the pairing was right. A
seam is exactly what a per-module test cannot see.

Slow by the standards of this suite and deliberately so: it runs the real
pipeline four times over, writes real files, and shells out to real git.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from tests.unit.web.page import normalised, read

from dbtw.core.context import read_project
from dbtw.web import Source

FIXTURES = Path(__file__).parents[1] / "fixtures"

# One script that exercises every seam this walk has: a statement that becomes
# a model and asks an incremental question, a TRUNCATE+INSERT pair that folds
# two statements into one model, and a GRANT folded into a model's config.
SCRIPT = """\
-- nightly revenue load
INSERT INTO revenue_events
SELECT order_id, customer_id, amount, order_date
FROM raw_orders
WHERE order_date >= '2024-01-01';

TRUNCATE TABLE daily_revenue;
INSERT INTO daily_revenue (day, total)
SELECT order_date, SUM(amount) FROM revenue_events GROUP BY order_date;

GRANT SELECT ON revenue_events TO analyst;
"""

DESCRIPTION = "One row per revenue event, at the grain the source system writes it."


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the fixture dbt project, committed, so it can take a branch."""
    root = tmp_path_factory.mktemp("e2e") / "jaffle_shop"
    shutil.copytree(FIXTURES / "projects" / "jaffle_shop", root)

    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=root, capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "reader@example.invalid")
    git("config", "user.name", "The Reader")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        git("add", "--", str(path.relative_to(root)))
    git("commit", "-q", "-m", "the project as it was")
    return root


def test_a_reader_goes_from_an_empty_app_to_a_commit_on_their_own_branch(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from dbtw.web.app import create_app

    out = tmp_path_factory.mktemp("e2e-out") / "dbtw-out"
    source = Source(out=out)
    app = create_app(source)
    app.testing = True
    client = app.test_client()

    # 1. An app with nothing in it sends a reader to bring some.
    assert client.get("/").headers["Location"].endswith("/source")
    assert source.project is None

    # 2. Convert refuses without a project, in the engine's own words.
    refused = client.post("/source", data={"project": "", "pasted": SCRIPT})
    assert refused.status_code == 400
    # Through a local: asserting `source.session is None` here narrows the
    # attribute for the rest of this function, and the POST below that fills
    # it in is invisible to a type checker -- which then reads every later
    # use as unreachable.
    after_refusal = source.session
    assert after_refusal is None

    # 3. The project and the SQL together open the conversation.
    started = client.post("/source", data={"project": str(project), "pasted": SCRIPT})
    assert started.status_code == 302
    assert source.project == project
    session = source.session
    assert session is not None

    # 4. The first screen says what was read out of their dbt_project.yml,
    #    with the evidence for each -- so a reader can check the conventions
    #    every later screen's proposals rest on.
    conventions = read(client.get("/project").get_data(as_text=True))
    detections = read_project(project).detections
    assert len(detections) > 1, "a real project should yield more than one convention"
    listed = sum(1 for item in conventions.items if item == "detection")
    assert listed == len(detections)
    assert dict(conventions.counts)["detection"] == str(listed)
    shown = {normalised(run) for run in conventions.engine}
    for detection in detections:
        assert detection.key in shown, f"{detection.key} is read and never shown"
        assert normalised(detection.evidence) in shown, f"{detection.key} is shown with no evidence"

    # 5. Every question this conversion asks is answerable, with the kind and
    #    the columns the question itself says it takes. Accepting a refusal
    #    here would make this step pass on a walk where nothing could be
    #    answered at all.
    view = session.view()
    assert view.questions, "this script should ask at least one question"
    for question in view.questions:
        option = question.options[0]
        prompt = view.prompts.get(question.key, {}).get(option.kind, "")
        data = {"key": question.key, "kind": option.kind}
        if prompt:
            subject = question.subject
            assert subject is not None and subject.candidates, (
                f"{question.key} asks for columns and offers none"
            )
            data["typed"] = subject.candidates[0]
        answered = client.post("/answer", data=data)
        assert answered.status_code == 302, (
            f"{question.key} refused {option.kind}: {read(answered.get_data(as_text=True)).engine}"
        )
        assert question.key in session.answers

    # 6. Describe the model the reader cares about.
    names = [model.name for model in session.view().change.models]
    assert (
        client.post("/describe", data={"model": names[0], "text": DESCRIPTION}).status_code == 302
    )

    # 7. Every screen of the walk renders.
    for url in ("/project", "/", "/describe", "/caveats", "/changed", "/files", "/done"):
        assert client.get(url).status_code == 200, url

    # 8. Write, and the files are on disk with the reader's own words in them.
    assert client.post("/write").status_code == 200
    written = {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in out.rglob("*")
        if p.is_file()
    }
    assert "CONVERSION_REPORT.md" in written
    models = [name for name in written if name.endswith(".sql")]
    assert models, f"no model files written: {sorted(written)}"
    schema = next(text for name, text in written.items() if name.endswith(f"{names[0]}.yml"))
    assert yaml.safe_load(schema)["models"][0]["description"] == DESCRIPTION

    # 9. Nothing has touched the project yet.
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True
        ).stdout.strip()
        == ""
    )

    # 10. Deliver: a branch in their own repository, holding those files.
    delivered = client.post("/deliver")
    assert delivered.status_code == 200
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=project, capture_output=True, text=True
    ).stdout.strip()
    assert branch.startswith("dbtw/")
    committed = subprocess.run(
        ["git", "show", "--stat", "--name-only", "--format=%an%n%s", "HEAD"],
        cwd=project,
        capture_output=True,
        text=True,
    ).stdout
    assert "The Reader" in committed, "the commit must be authored by the reader, not by dbtw"
    for name in written:
        assert name in committed, f"{name} was written but not delivered"
        assert (project / name).read_text(encoding="utf-8") == written[name]

    # 11. The tree is clean: everything that landed is in the commit.
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True
        ).stdout.strip()
        == ""
    )
