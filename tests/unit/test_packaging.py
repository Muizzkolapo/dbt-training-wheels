import tomllib
from pathlib import Path

import dbtw

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def test_package_importable_and_versioned():
    assert dbtw.__version__.startswith("0.1")


def test_the_dev_extra_installs_everything_the_test_suite_needs():
    """CI (`.github/workflows/ci.yml`) and the README both build a development
    environment with `pip install -e ".[dev]"` and nothing else, so `dev` is
    the whole declared environment this suite runs in. `dbtw web` imports
    Flask at startup and its tests drive that path, which makes Flask a
    dependency of the suite rather than only of the feature -- left out of
    `dev`, eight tests fail and pyright reports the import unresolved in every
    environment but a hand-built one.

    Declared as the `web` extra by reference rather than by repeating
    `flask>=3`, so the version constraint has one home and the two cannot
    drift apart.
    """
    extras = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    assert "dbt-training-wheels[web]" in extras["dev"]
    assert extras["web"] == ["flask>=3"]
