"""Shared helpers for git-backed tests."""

import subprocess

DBT_PROJECT_YML = """name: my_dbt_project

models:
  my_dbt_project:
    +materialized: table

seeds:
  my_dbt_project:
    +quote_columns: false
"""

PROJECT_PATH = "dbt_projects/analytics"


def git(args, cwd):
    """Run a git command, raising on failure."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def model_file(domain, name):
    """Build a generated-file dict for a model in a domain."""
    return {
        "path": f"{PROJECT_PATH}/models/{domain}/{name}.sql",
        "type": "model",
        "content": f"select 1 as id -- {name}\n",
    }


def files_on(repo, branch):
    """List the files present on a branch."""
    return set(git(["ls-tree", "-r", "--name-only", branch], cwd=repo).stdout.split())


def file_content_on(repo, branch, path):
    """Read a file's contents from a branch."""
    return git(["show", f"{branch}:{path}"], cwd=repo).stdout


def branch_exists(repo, branch):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=str(repo),
        capture_output=True,
    )
    return result.returncode == 0


def is_ancestor(repo, ancestor_sha, descendant_sha):
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        cwd=str(repo),
        capture_output=True,
    )
    return result.returncode == 0
