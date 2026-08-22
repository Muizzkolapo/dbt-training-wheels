"""Create and link stacked pull requests with the `gh stack` extension.

Branches are created and pushed by github_service using git over SSH, which needs no
GitHub credentials. Opening pull requests does need them, so this module is a separate,
optional layer: when `gh` is present and authenticated, `gh stack link` turns the
branches we just pushed into a linked stack on GitHub; when it isn't, callers fall back
to the compare URLs.

`gh stack link` is used rather than `init`/`submit` because it explicitly "does not rely
on gh-stack local tracking state" - it is meant for branches produced by external
tooling, which is exactly what we do. That keeps this container stateless.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# `gh stack` is only available from this version of the CLI onwards
MINIMUM_GH_VERSION = (2, 90, 0)

_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_PR_URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/pull/\d+")


@dataclass
class GhStackCapability:
    """Whether this environment can create stacked PRs, and why not if it can't."""

    available: bool
    reason: str = ""
    version: tuple[int, int, int] | None = None

    @property
    def version_string(self) -> str:
        return ".".join(str(part) for part in self.version) if self.version else "unknown"


def _run(args: list[str], cwd: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command without raising, so callers can inspect the failure."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _parse_version(output: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.search(output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def check_gh_stack(cwd: str | None = None) -> GhStackCapability:
    """Check whether `gh stack` can be used here.

    Verifies, in order: the CLI is installed, it is new enough, the extension is
    installed, and the CLI is authenticated. Authentication is the step that usually
    fails, because the deployment authenticates to GitHub with SSH keys, which `gh`
    does not read - it needs GH_TOKEN or a `gh auth login` session.

    Args:
        cwd: Directory to run the checks in (a clone, so repo detection works)

    Returns:
        GhStackCapability describing whether stacked PRs can be created
    """
    try:
        version_result = _run(["gh", "--version"], cwd=cwd)
    except FileNotFoundError:
        return GhStackCapability(False, "The GitHub CLI (gh) is not installed")
    except subprocess.TimeoutExpired:
        return GhStackCapability(False, "The GitHub CLI (gh) did not respond")

    if version_result.returncode != 0:
        return GhStackCapability(False, "Could not run the GitHub CLI (gh)")

    version = _parse_version(version_result.stdout)
    if version and version < MINIMUM_GH_VERSION:
        wanted = ".".join(str(part) for part in MINIMUM_GH_VERSION)
        found = ".".join(str(part) for part in version)
        return GhStackCapability(False, f"gh {found} is too old for stacked PRs (needs {wanted}+)", version)

    # Auth is checked before the extension because `gh extension list` can come back
    # empty simply for lack of credentials, which would report the wrong cause
    auth = _run(["gh", "auth", "status"], cwd=cwd)
    if auth.returncode != 0:
        return GhStackCapability(
            False,
            "The GitHub CLI is not authenticated - set GH_TOKEN, or run `gh auth login` where DBT Training Wheels runs",
            version,
        )

    # Ask the extension directly rather than parsing `gh extension list` - a binary
    # extension dropped into place may not list, but it still runs
    probe = _run(["gh", "stack", "--help"], cwd=cwd)
    if probe.returncode != 0:
        return GhStackCapability(
            False,
            "The gh-stack extension is not installed (gh extension install github/gh-stack)",
            version,
        )

    return GhStackCapability(True, version=version)


def link_existing_stack(branches: list[str], repository: str, base_branch: str | None = None) -> dict:
    """
    Link branches that are already pushed into a stack on GitHub.

    For repairing a deploy that pushed correctly but couldn't link - `gh stack link`
    reuses whatever pull requests those branches already have, so this adopts them
    rather than opening duplicates. Needs its own clone because gh resolves the
    repository from the working directory.

    Args:
        branches: Branch names in merge order (bottom of the stack first)
        repository: GitHub repo in "owner/repo" format
        base_branch: Trunk for the bottom of the stack

    Returns:
        Same shape as link_stack()
    """
    import tempfile

    from dbt_training_wheels.services.github_service import _clone_repo

    capability = check_gh_stack()
    if not capability.available:
        return {"success": False, "reason": capability.reason, "pull_requests": []}

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            _clone_repo(repository, base_branch or "main", tmpdir)
        except Exception as e:
            return {"success": False, "reason": f"Could not clone {repository}: {e}", "pull_requests": []}

        return link_stack(branches, tmpdir, base_branch=base_branch, open_prs=True)


def link_stack(
    branches: list[str],
    repo_dir: str,
    base_branch: str | None = None,
    open_prs: bool = True,
) -> dict:
    """
    Turn already-pushed branches into a linked stack of pull requests.

    `gh stack link` reuses any open PR a branch already has, creates one with the
    correct base for any branch that doesn't, and links them all into a stack on
    GitHub. Branches must be given bottom to top.

    Args:
        branches: Branch names in merge order (bottom of the stack first)
        repo_dir: A clone of the target repository, used for remote detection
        base_branch: Trunk for the bottom of the stack (defaults to the repo default)
        open_prs: Mark the PRs ready for review; without this they are created as drafts

    Returns:
        Dict with 'success', the PR urls found in the output, and the raw output for
        logging. Never raises - PR creation is best effort on top of a completed push.
    """
    if not branches:
        return {"success": False, "reason": "No branches to link", "pull_requests": []}

    command = ["gh", "stack", "link"]
    if base_branch:
        command += ["--base", base_branch]
    if open_prs:
        command.append("--open")
    command += branches

    logger.info(f"[gh stack] Linking {len(branches)} branches into a stack: {' '.join(branches)}")

    try:
        result = _run(command, cwd=repo_dir, timeout=180)
    except FileNotFoundError:
        return {"success": False, "reason": "The GitHub CLI (gh) is not installed", "pull_requests": []}
    except subprocess.TimeoutExpired:
        return {"success": False, "reason": "gh stack link timed out", "pull_requests": []}

    output = f"{result.stdout}\n{result.stderr}".strip()

    if result.returncode != 0:
        logger.warning(f"[gh stack] link failed (exit {result.returncode}): {output}")
        return {"success": False, "reason": output or "gh stack link failed", "pull_requests": []}

    pull_requests = _PR_URL_PATTERN.findall(output)
    logger.info(f"[gh stack] ✓ Linked stack with {len(pull_requests)} pull request(s)")

    return {"success": True, "pull_requests": pull_requests, "output": output}
