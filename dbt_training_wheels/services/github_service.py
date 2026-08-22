"""Service for GitHub operations via SSH (no tokens needed!).

All GitHub operations use git commands with SSH keys mounted in Docker.
No GitHub API tokens required.
"""

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import GitHubConfig

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Exception for GitHub/Git errors."""

    def __init__(self, message: str, status_code: int | None = None, response: dict[str, Any] | None = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a git command and return result."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ssh_url(repository: str) -> str:
    """Build the SSH clone URL for a repository.

    Kept as a separate function so tests can point clones at a local repo.
    """
    return f"git@github.com:{repository}.git"


def _clone_repo(repository: str, branch: str, tmpdir: str) -> None:
    """Clone a repository using SSH."""
    url = _ssh_url(repository)
    logger.info(f"[GitHub SSH] Cloning {url} (branch: {branch})")
    _run_git(["clone", "--depth", "1", "--branch", branch, url, tmpdir])


def _configure_git_identity(repo_dir: str) -> None:
    """Set the committer identity used for generated commits."""
    subprocess.run(["git", "config", "user.email", "dbt_training_wheels@localhost"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DBT Training Wheels"], cwd=repo_dir, capture_output=True)


def _write_files(files: list[dict[str, Any]], repo_dir: str, models_path: str = "") -> list[str]:
    """Write generated files into the clone.

    Args:
        files: List of file dicts with 'path' and 'content' keys
        repo_dir: Path to the cloned repository
        models_path: Optional prefix to prepend when a path doesn't already start with it

    Returns:
        List of repo-relative paths written
    """
    files_written = []
    for file_info in files:
        file_path = file_info.get("path", "")
        content = file_info.get("content", "")

        if not file_path:
            continue

        # Add models_path prefix if provided and not already in path
        if models_path and not file_path.startswith(models_path):
            full_path = Path(repo_dir) / models_path / file_path
        else:
            full_path = Path(repo_dir) / file_path

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        files_written.append(str(file_path))
        logger.debug(f"[GitHub SSH] Wrote file: {full_path}")

    return files_written


def _has_changes(repo_dir: str) -> bool:
    """Check whether the working tree or index has anything to commit."""
    result = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
    return bool(result.stdout.strip())


def _apply_precommit_fixes(files_written: list[str], repo_dir: str) -> None:
    """Run the target repo's pre-commit hooks over the written files and commit any fixes."""
    if not (Path(repo_dir) / ".pre-commit-config.yaml").exists():
        logger.info("[GitHub SSH] No .pre-commit-config.yaml in target repo, skipping pre-commit")
        return

    precommit_result = subprocess.run(
        ["pre-commit", "run", "--files"] + files_written, cwd=repo_dir, capture_output=True, text=True
    )
    logger.info(f"[GitHub SSH] pre-commit exit code: {precommit_result.returncode}")
    logger.info(f"[GitHub SSH] pre-commit stdout: {precommit_result.stdout}")
    logger.info(f"[GitHub SSH] pre-commit stderr: {precommit_result.stderr}")

    _run_git(["add", "-A"], cwd=repo_dir)
    try:
        _run_git(["commit", "-m", "Apply pre-commit fixes"], cwd=repo_dir)
        logger.info("[GitHub SSH] Applied pre-commit fixes")
    except subprocess.CalledProcessError:
        pass


def _project_root_from_paths(files_written: list[str]) -> str:
    """Derive the dbt project root from generated paths.

    dbt_project.yml sits at the same level as the models/ folder, so the project
    root is whatever precedes 'models/' in a generated file path.
    """
    if not files_written:
        return ""

    sample_path = files_written[0]
    models_idx = sample_path.find("/models/")
    if models_idx == -1:
        models_idx = sample_path.find("models/")
        return sample_path[:models_idx] if models_idx > 0 else ""
    return sample_path[:models_idx]


class BranchesExistError(GitHubError):
    """The branches this push would create are already on the remote.

    Recoverable: pushing again with force replaces them, which updates any open PRs in
    place rather than opening new ones. Carried separately from other git failures so
    callers can offer that choice instead of showing a dead end.
    """

    def __init__(self, branches: list[str]):
        self.branches = branches
        super().__init__(f"Branches already exist on the remote: {', '.join(branches)}")


def _resolve_base_branch(repository: str, requested: str | None, config_default: str | None) -> str:
    """
    Decide which branch a push should build on, and check it exists.

    A stack normally lands on the repository's default branch, but it can target any
    branch - stacking onto an existing feature branch, for instance. Checked up front
    because otherwise the clone fails with a bare git error that reads like a push
    problem.

    Args:
        repository: GitHub repo in "owner/repo" format
        requested: Branch the caller asked for, if any
        config_default: The configured default branch

    Returns:
        The branch to base on

    Raises:
        GitHubError: If the requested branch doesn't exist on the remote
    """
    base = (requested or "").strip() or config_default or "main"

    if requested and requested.strip() and requested.strip() != (config_default or "main"):
        result = subprocess.run(
            ["git", "ls-remote", "--heads", _ssh_url(repository), base],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and not result.stdout.strip():
            raise GitHubError(
                f"Base branch '{base}' doesn't exist in {repository}. "
                "Create it first, or leave the base empty to use the default branch."
            )

    return base


def _remote_branch_shas(repo_dir: str, branches: list[str]) -> dict[str, str]:
    """Which of these branches exist on the remote, and at which commit."""
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", *branches],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(f"[GitHub SSH] Could not list remote branches: {result.stderr}")
        return {}

    shas = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            shas[ref[len("refs/heads/") :]] = sha.strip()
    return shas


def _push_branches(repo_dir: str, branches: list[str], force: bool = False) -> None:
    """
    Push branches atomically, replacing existing ones only when asked to.

    Regenerating a conversion produces the same branch names, so a second deploy would
    otherwise be rejected as non-fast-forward - and atomically, taking the whole stack
    with it. With force, each existing branch is replaced under a --force-with-lease
    pinned to the sha we just observed, so a branch that moved in the meantime aborts
    the push rather than being silently overwritten.

    Args:
        repo_dir: The clone to push from
        branches: Branch names to push
        force: Replace branches that already exist on the remote

    Raises:
        BranchesExistError: If any branch exists and force wasn't requested
    """
    existing = _remote_branch_shas(repo_dir, branches)

    if existing and not force:
        raise BranchesExistError(sorted(existing))

    args = ["push", "--atomic", "-u", "origin"]
    for branch in branches:
        if branch in existing:
            # Pin the lease to what we just read, so a concurrent update aborts the push
            args.append(f"--force-with-lease={branch}:{existing[branch]}")
            logger.info(f"[GitHub SSH] Replacing existing branch '{branch}' (was {existing[branch][:8]})")

    _run_git(args + branches, cwd=repo_dir)


def _head_sha(repo_dir: str) -> str:
    """Return the current HEAD sha, or 'unknown' if it can't be read."""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _add_domain_block(
    repo_dir: str,
    domain_area: str,
    dbt_project_name: str,
    active_layers: list[str] | None,
    naming_prefix: str,
    files_written: list[str],
) -> None:
    """Add the domain block to dbt_project.yml, appending it to files_written if changed."""
    if not (domain_area and dbt_project_name and files_written):
        return

    project_root_rel = _project_root_from_paths(files_written)
    logger.info(f"[GitHub SSH] Updating dbt_project.yml at: {project_root_rel or '(repo root)'}")

    modified = _update_dbt_project_yml(
        repo_dir,
        domain_area,
        dbt_project_name,
        active_layers or [],
        naming_prefix,
        base_path=project_root_rel,
    )
    if modified:
        files_written.append(f"{project_root_rel}/dbt_project.yml" if project_root_rel else "dbt_project.yml")


def _update_dbt_project_yml(
    repo_dir: str,
    domain_area: str,
    dbt_project_name: str,
    active_layers: list[str],
    naming_prefix: str = "dbt_training_wheels",
    base_path: str = "",
) -> bool:
    """Add a domain block to the existing dbt_project.yml in the cloned repo.

    Searches for the dbt_project.yml file, checks if the domain already exists,
    and if not, appends the domain configuration block under the project's models section.

    Args:
        repo_dir: Path to the cloned repository
        domain_area: Domain name to add (e.g., "sales")
        dbt_project_name: dbt project name (top-level key under models:)
        active_layers: Layers that have models (e.g., ["staging", "mart"])
        naming_prefix: Tag prefix for the domain (e.g., "dbt_training_wheels")
        base_path: Path within repo to the dbt project (e.g., "dbt_projects/analytics")

    Returns:
        True if the file was modified, False otherwise
    """
    # Lazy import to avoid circular dependency: github_service <-> file_generator
    from dbt_training_wheels.services.file_generator import generate_dbt_project_domain_block

    # dbt_project.yml lives inside the project's base_path, not at repo root
    project_root = Path(repo_dir) / base_path if base_path else Path(repo_dir)
    dbt_project_path = project_root / "dbt_project.yml"
    if not dbt_project_path.exists():
        logger.warning("[GitHub SSH] No dbt_project.yml found in repo, skipping domain block update")
        return False

    content = dbt_project_path.read_text()

    # Check if this domain already exists as an exact YAML key at 4-space indentation.
    # Regex with exact match prevents partial hits (e.g., "sale" matching "sales_eu:").
    domain_re = re.compile(rf"^ {{4}}{re.escape(domain_area)}:\s*$", re.MULTILINE)
    if domain_re.search(content):
        logger.info(f"[GitHub SSH] Domain '{domain_area}' already exists in dbt_project.yml, skipping")
        return False

    # Generate the domain block
    domain_block = generate_dbt_project_domain_block(
        domain_area=domain_area,
        layers=active_layers,
        naming_prefix=naming_prefix,
    )

    # Anchor search to the "models:" section so we don't match dbt_project_name
    # appearing elsewhere (comments, variables, other top-level sections).
    # Use regex to find "models:" at the start of a line (handles both start-of-file and mid-file)
    models_match = re.search(r"^models:", content, re.MULTILINE)
    if not models_match:
        logger.warning("[GitHub SSH] No 'models:' section found in dbt_project.yml, skipping")
        return False
    models_section_idx = models_match.start()

    # Find the project name key within the models section
    project_key = f"  {dbt_project_name}:"
    project_key_idx = content.find(project_key, models_section_idx)
    if project_key_idx == -1:
        logger.warning(f"[GitHub SSH] Could not find '{dbt_project_name}' under models: in dbt_project.yml, skipping")
        return False

    # Find the next top-level key after the project section
    # Top-level keys are lines that start with a non-space character followed by ':'
    # (e.g., "seeds:", "tests:", "snapshots:")
    search_start = project_key_idx + len(project_key)
    lines = content[search_start:].split("\n")
    insert_offset = search_start
    for line in lines:
        if line and not line.startswith(" ") and not line.startswith("#") and ":" in line:
            break
        insert_offset += len(line) + 1  # +1 for the newline

    # Insert the domain block before the next top-level section
    updated_content = content[:insert_offset] + domain_block + content[insert_offset:]
    dbt_project_path.write_text(updated_content)
    logger.info(f"[GitHub SSH] ✅ Added domain '{domain_area}' to dbt_project.yml")
    return True


def push_to_github(
    files: list[dict[str, Any]],
    branch_name: str,
    commit_message: str,
    config: "GitHubConfig",
    models_path: str = "",
    create_pr: bool = False,
    pr_title: str | None = None,
    pr_body: str | None = None,
    domain_area: str = "",
    dbt_project_name: str = "",
    active_layers: list[str] | None = None,
    naming_prefix: str = "dbt_training_wheels",
    force: bool = False,
    base_branch: str | None = None,
) -> dict[str, Any]:
    """
    Push files to GitHub using git commands with SSH keys.

    No GitHub token needed! Uses SSH keys mounted in Docker.

    Args:
        files: List of file dicts with 'path' and 'content' keys
        branch_name: Target branch name
        commit_message: Commit message
        config: GitHubConfig object
        models_path: Path prefix for models (usually empty - paths already include base_path)
        create_pr: Whether to create a PR after pushing
        pr_title: Optional PR title
        pr_body: Optional PR body
        domain_area: Optional domain name to add to dbt_project.yml
        dbt_project_name: dbt project name (top-level key in dbt_project.yml models section)
        active_layers: List of active layers (e.g., ["staging", "intermediate", "mart"])
        naming_prefix: Tag prefix for the domain in dbt_project.yml (e.g., "dbt_training_wheels")

    Returns:
        Dict with push results
    """
    repository = config.repository
    if not repository:
        raise GitHubError("GitHub repository not configured. Set github.repository in dbt_training_wheels_config.yaml")

    default_branch = _resolve_base_branch(repository, base_branch, config.default_branch)

    logger.info(f"[GitHub SSH] Pushing {len(files)} files to {repository} branch '{branch_name}'")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Clone the repo
            _clone_repo(repository, default_branch, tmpdir)
            _configure_git_identity(tmpdir)

            # Create and checkout branch
            logger.info(f"[GitHub SSH] Creating branch '{branch_name}'")
            _run_git(["checkout", "-b", branch_name], cwd=tmpdir)

            # Write files
            files_written = _write_files(files, tmpdir, models_path)

            # Update dbt_project.yml with the new domain block if domain_area is provided
            _add_domain_block(tmpdir, domain_area, dbt_project_name, active_layers, naming_prefix, files_written)

            # Git add
            _run_git(["add", "-A"], cwd=tmpdir)

            # Commit - content
            logger.info(f"[GitHub SSH] Committing {len(files_written)} files")
            _run_git(["commit", "-m", commit_message], cwd=tmpdir)

            # Apply pre-commit fixes from target repo
            _apply_precommit_fixes(files_written, tmpdir)

            # Push
            logger.info(f"[GitHub SSH] Pushing to origin/{branch_name}")
            _push_branches(tmpdir, [branch_name], force=force)

            commit_sha = _head_sha(tmpdir)

            result: dict[str, Any] = {
                "success": True,
                "branch": branch_name,
                "branch_url": f"https://github.com/{repository}/tree/{branch_name}",
                "commit_sha": commit_sha,
                "files_pushed": len(files_written),
            }

            # Create PR if requested
            if create_pr or config.auto_create_pr:
                pr_result = _create_pr_via_gh_cli(
                    repository, branch_name, default_branch, pr_title or commit_message, pr_body, tmpdir
                )
                if pr_result:
                    result["pull_request"] = pr_result
                else:
                    result["pull_request_note"] = (
                        f"Create PR manually: https://github.com/{repository}/compare/{default_branch}...{branch_name}"
                    )

            logger.info(f"[GitHub SSH] ✓ Successfully pushed to {branch_name}")
            return result

        except BranchesExistError:
            raise
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or e.stdout or str(e)
            logger.error(f"[GitHub SSH] Git command failed: {error_msg}")
            raise GitHubError(f"Git push failed: {error_msg}") from e
        except subprocess.TimeoutExpired:
            logger.error("[GitHub SSH] Git operation timed out")
            raise GitHubError("Git operation timed out") from None


@dataclass
class StackEntry:
    """One PR-sized unit of a stacked push.

    Entries are pushed in list order and each branch is created from the previous
    entry's branch, so reviewers can merge the stack bottom-up.
    """

    # Slug appended to the branch name, e.g. "customer" -> dbt_training_wheels/churn--customer
    name: str

    # Files for this entry only, in the same {path, content} shape as push_to_github
    files: list[dict[str, Any]] = field(default_factory=list)

    commit_message: str = ""

    # Optional dbt_project.yml domain block for this entry
    domain_area: str = ""
    active_layers: list[str] = field(default_factory=list)


def _slugify_branch_part(value: str) -> str:
    """Reduce a string to something safe to use inside a git branch name."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned)


def _link_stack_prs(branches: list[str], repo_dir: str, base_branch: str) -> dict[str, Any]:
    """Turn pushed branches into a linked stack of PRs, if `gh stack` can be used.

    Best effort by design: the branches are already pushed, so a missing or
    unauthenticated CLI degrades to the compare URLs rather than failing the deploy.
    """
    from dbt_training_wheels.services.gh_stack_service import check_gh_stack, link_stack

    capability = check_gh_stack(cwd=repo_dir)
    if not capability.available:
        logger.info(f"[GitHub SSH] Skipping PR creation: {capability.reason}")
        return {"pull_requests": [], "pr_linking": {"success": False, "reason": capability.reason}}

    linked = link_stack(branches, repo_dir, base_branch=base_branch, open_prs=True)
    if not linked["success"]:
        logger.warning(f"[GitHub SSH] Stack pushed but PR linking failed: {linked.get('reason')}")

    return {
        "pull_requests": linked.get("pull_requests", []),
        "pr_linking": {"success": linked["success"], "reason": linked.get("reason", "")},
    }


def push_stack_to_github(
    entries: list[StackEntry],
    base_slug: str,
    config: "GitHubConfig",
    models_path: str = "",
    dbt_project_name: str = "",
    naming_prefix: str = "dbt_training_wheels",
    create_pr: bool = False,
    force: bool = False,
    base_branch: str | None = None,
) -> dict[str, Any]:
    """
    Push a stack of dependent branches, each based on the previous one.

    Branch N is created from branch N-1 (the first from the default branch), so each
    branch's diff contains only its own entry's files. Everything is pushed atomically -
    either the whole stack lands or none of it does.

    Pushing needs only the SSH key. Creating the pull requests needs GitHub
    credentials, so it is attempted separately via `gh stack link` and degrades to the
    per-entry compare URLs when the CLI isn't available or authenticated.

    Args:
        entries: Stack entries in merge order (first entry merges first)
        base_slug: Shared branch name stem, e.g. "churn" -> dbt_training_wheels/churn--customer
        config: GitHubConfig for the target repository
        models_path: Path prefix for models (usually empty - paths include base_path)
        dbt_project_name: dbt project name (top-level key in dbt_project.yml models section)
        naming_prefix: Tag prefix for domain blocks in dbt_project.yml
        create_pr: Also create and link the pull requests on GitHub

    Returns:
        Dict with the ordered stack, each item carrying its branch, base and compare
        URL, plus 'pull_requests' and 'pr_linking' when create_pr was requested

    Raises:
        GitHubError: If the repository isn't configured, or nothing could be pushed
    """
    repository = config.repository
    if not repository:
        raise GitHubError("GitHub repository not configured. Set github.repository in dbt_training_wheels_config.yaml")

    default_branch = _resolve_base_branch(repository, base_branch, config.default_branch)
    branch_prefix = config.branch_prefix or ""
    base_slug = _slugify_branch_part(base_slug) or "stack"

    populated = [e for e in entries if e.files]
    for empty in [e for e in entries if not e.files]:
        logger.warning(f"[GitHub SSH] Stack entry '{empty.name}' has no files, skipping")

    if not populated:
        raise GitHubError("No files to push - every stack entry was empty")

    logger.info(f"[GitHub SSH] Pushing a stack of {len(populated)} branches to {repository}")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            _clone_repo(repository, default_branch, tmpdir)
            _configure_git_identity(tmpdir)

            stack: list[dict[str, Any]] = []
            parent_branch = default_branch

            for entry in populated:
                branch_name = f"{branch_prefix}{base_slug}--{_slugify_branch_part(entry.name)}"

                logger.info(f"[GitHub SSH] Creating branch '{branch_name}' from '{parent_branch}'")
                _run_git(["checkout", "-b", branch_name, parent_branch], cwd=tmpdir)

                files_written = _write_files(entry.files, tmpdir, models_path)

                # Each entry adds its own domain block. Because the branches chain, an
                # entry sees the blocks added by its ancestors and appends to them.
                _add_domain_block(
                    tmpdir,
                    entry.domain_area,
                    dbt_project_name,
                    entry.active_layers,
                    naming_prefix,
                    files_written,
                )

                _run_git(["add", "-A"], cwd=tmpdir)
                if not _has_changes(tmpdir):
                    logger.warning(
                        f"[GitHub SSH] Stack entry '{entry.name}' matches the target repository, skipping branch"
                    )
                    _run_git(["checkout", parent_branch], cwd=tmpdir)
                    _run_git(["branch", "-D", branch_name], cwd=tmpdir)
                    continue

                logger.info(f"[GitHub SSH] Committing {len(files_written)} files to '{branch_name}'")
                _run_git(["commit", "-m", entry.commit_message or f"Add dbt models: {entry.name}"], cwd=tmpdir)

                _apply_precommit_fixes(files_written, tmpdir)

                stack.append(
                    {
                        "name": entry.name,
                        "branch": branch_name,
                        "base": parent_branch,
                        "branch_url": f"https://github.com/{repository}/tree/{branch_name}",
                        "compare_url": (
                            f"https://github.com/{repository}/compare/{parent_branch}...{branch_name}?expand=1"
                        ),
                        "commit_sha": _head_sha(tmpdir),
                        "files_pushed": len(files_written),
                    }
                )

                parent_branch = branch_name

            if not stack:
                raise GitHubError("Nothing to push - all generated files already match the target repository")

            branches = [item["branch"] for item in stack]
            logger.info(f"[GitHub SSH] Pushing {len(branches)} branches atomically: {', '.join(branches)}")
            # git's push is atomic; gh stack push explicitly isn't, so the push stays here
            _push_branches(tmpdir, branches, force=force)

            logger.info(f"[GitHub SSH] ✓ Successfully pushed stack of {len(stack)} branches")
            result: dict[str, Any] = {
                "success": True,
                "base_branch": default_branch,
                "branches_pushed": len(stack),
                "stack": stack,
            }

            if create_pr:
                result.update(_link_stack_prs(branches, tmpdir, default_branch))

            return result

        except BranchesExistError:
            raise
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or e.stdout or str(e)
            logger.error(f"[GitHub SSH] Git command failed: {error_msg}")
            raise GitHubError(f"Git push failed: {error_msg}") from e
        except subprocess.TimeoutExpired:
            logger.error("[GitHub SSH] Git operation timed out")
            raise GitHubError("Git operation timed out") from None


def _create_pr_via_gh_cli(
    repository: str,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str | None,
    cwd: str,
) -> dict[str, Any] | None:
    """Create PR using gh CLI (if available)."""
    try:
        cmd = [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base_branch,
            "--head",
            branch_name,
            "--title",
            title,
        ]
        if body:
            cmd.extend(["--body", body])

        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            pr_url = result.stdout.strip()
            logger.info(f"[GitHub SSH] ✓ Created PR: {pr_url}")
            return {"url": pr_url}
        else:
            logger.warning(f"[GitHub SSH] gh CLI failed: {result.stderr}")
            return None

    except FileNotFoundError:
        logger.info("[GitHub SSH] gh CLI not available, skipping PR creation")
        return None
    except Exception as e:
        logger.warning(f"[GitHub SSH] Failed to create PR: {e}")
        return None


def scan_existing_sources_remote(
    repository: str,
    base_path: str,
    models_path: str = "models",
    default_branch: str = "main",
) -> set[tuple[str, str]]:
    """
    Scan remote repo for existing dbt source definitions using SSH.

    Args:
        repository: GitHub repo in "owner/repo" format
        base_path: Base path within repo (e.g., "dbt_projects/myproject")
        models_path: Path to models directory
        default_branch: Branch to scan

    Returns:
        Set of (source_name, table_name) tuples
    """
    from dbt_training_wheels.services.file_generator import scan_existing_sources

    logger.info(f"[GitHub SSH] Scanning {repository} for existing sources")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            _clone_repo(repository, default_branch, tmpdir)

            # Scan the models path within cloned repo
            scan_path = Path(tmpdir) / base_path if base_path else Path(tmpdir)
            if not scan_path.exists():
                logger.warning(f"[GitHub SSH] Path {base_path} not found in repo")
                return set()

            return scan_existing_sources(str(scan_path), models_path)

        except subprocess.CalledProcessError as e:
            logger.error(f"[GitHub SSH] Clone failed: {e.stderr}")
            return set()
        except Exception as e:
            logger.error(f"[GitHub SSH] Source scan failed: {e}")
            return set()


def scan_public_models_remote(
    repository: str,
    base_path: str,
    default_branch: str = "main",
) -> set[str]:
    """
    Scan remote repo for public dbt models using SSH.

    Args:
        repository: GitHub repo in "owner/repo" format
        base_path: Path within repo to scan (e.g., "dbt_projects/analytics_platform")
        default_branch: Branch to scan

    Returns:
        Set of public model names
    """
    from dbt_training_wheels.services.file_generator import scan_public_models

    logger.info(f"[GitHub SSH] Scanning {repository} for public models at {base_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            _clone_repo(repository, default_branch, tmpdir)

            # Scan the base_path within cloned repo
            scan_path = Path(tmpdir) / base_path
            if not scan_path.exists():
                logger.warning(f"[GitHub SSH] Path {base_path} not found in repo")
                return set()

            return scan_public_models(str(scan_path))

        except subprocess.CalledProcessError as e:
            logger.error(f"[GitHub SSH] Clone failed: {e.stderr}")
            return set()
        except Exception as e:
            logger.error(f"[GitHub SSH] Public model scan failed: {e}")
            return set()


# Backward compatibility - keep old class name but make it use SSH
class GitHubService:
    """
    GitHub service using SSH keys (no token required!).

    This is a simplified version that uses git commands with SSH keys
    instead of the GitHub REST API.
    """

    def __init__(self, config: "GitHubConfig"):
        """Initialize with config."""
        self.config = config
        self.repository = config.repository
        self.default_branch = config.default_branch or "main"
        self.base_path = getattr(config, "base_path", "")

        if not self.repository:
            raise GitHubError("GitHub repository not configured")

        logger.info(f"[GitHub SSH] Service initialized for {self.repository}")

    def push_files(
        self,
        files: list[dict[str, Any]],
        branch_name: str,
        commit_message: str,
        models_path: str = "",
    ) -> dict[str, Any]:
        """Push files using SSH."""
        return push_to_github(
            files=files,
            branch_name=branch_name,
            commit_message=commit_message,
            config=self.config,
            models_path=models_path,
            create_pr=False,
        )

    def create_pull_request(
        self,
        branch_name: str,
        title: str,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Create PR using gh CLI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _clone_repo(self.repository, self.default_branch, tmpdir)
            result = _create_pr_via_gh_cli(self.repository, branch_name, self.default_branch, title, body, tmpdir)
            if result:
                return result
            return {
                "url": f"https://github.com/{self.repository}/compare/{self.default_branch}...{branch_name}",
                "note": "PR not auto-created. Use link to create manually.",
            }

    def scan_existing_sources_remote(self, models_path: str = "models") -> set[tuple[str, str]]:
        """Scan for existing sources using SSH."""
        return scan_existing_sources_remote(self.repository, self.base_path, models_path, self.default_branch)

    def scan_public_models_remote(self, base_path: str) -> set[str]:
        """Scan for public models using SSH."""
        return scan_public_models_remote(self.repository, base_path, self.default_branch)
