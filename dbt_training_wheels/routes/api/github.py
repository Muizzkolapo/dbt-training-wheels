"""GitHub API routes for DBT Training Wheels.

Endpoints for GitHub integration - pushing files and checking status.
"""

import logging
from datetime import datetime
from typing import cast

from flask import Blueprint, jsonify, request

from dbt_training_wheels.config import get_org_config
from dbt_training_wheels.exceptions import ConfigurationError, FileSystemError, ValidationError
from dbt_training_wheels.models.types import QueryInput
from dbt_training_wheels.services.analysis_service import analyze_query
from dbt_training_wheels.services.domain_resolver import attribute_models_to_domains, group_files_by_domain
from dbt_training_wheels.services.file_generator import generate_files_for_query
from dbt_training_wheels.services.query_service import get_query_by_id
from dbt_training_wheels.storage import FileSystemStorage
from dbt_training_wheels.utils import handle_route_errors

logger = logging.getLogger(__name__)

github_bp = Blueprint("github", __name__)

# Storage instance for model configs
_storage = FileSystemStorage()


def _branch_conflict_error(exc, already_pushed: str = ""):
    """Turn an existing-branch push rejection into a conflict the UI can offer to resolve.

    already_pushed names the domains that reached GitHub before the conflict, when a
    conversion deploys as several independent groups and a later one is rejected.
    """
    return ValidationError(
        user_message=(
            f"{len(exc.branches)} branch(es) from a previous deploy already exist: {', '.join(exc.branches)}"
        ),
        beginner_help=(
            (f"{already_pushed} already pushed successfully. " if already_pushed else "")
            + "Replacing them updates any open pull requests in place, keeping their numbers and review comments"
        ),
        common_fixes=[
            "Confirm to replace the existing branches",
            "Use a different branch name to deploy alongside them",
            "Delete the old branches on GitHub first",
        ],
        docs_anchor="github-integration",
        technical_message=str(exc),
        details={"conflicts": exc.branches, "can_overwrite": True, "already_pushed": already_pushed},
    )


@github_bp.route("/link-stack", methods=["POST"])
@handle_route_errors
def link_stack_endpoint():
    """
    Link already-pushed branches into a stack on GitHub.

    Correct base branches alone don't make a stack - GitHub's stack is an object
    created through the API, which is why a deploy that fell back to compare links
    leaves the pull requests unlinked. This repairs that without re-pushing: the
    branches keep their commits and any open pull requests are adopted.

    Request Body:
        {"branches": ["dbt_training_wheels/churn--customer", "dbt_training_wheels/churn--insurance"],
         "base_branch": "main"}
    """
    from dbt_training_wheels.services.gh_stack_service import link_existing_stack

    config = get_org_config()
    if not config or not config.github or not config.github.enabled:
        raise ConfigurationError(
            user_message="GitHub integration is not configured",
            beginner_help="Linking a stack needs the GitHub settings configured",
            common_fixes=["Set github.enabled and github.repository in dbt_training_wheels_config.yaml"],
            docs_anchor="github-integration",
            technical_message="GitHub config not enabled",
        )

    branches = (request.json or {}).get("branches") or []
    if len(branches) < 2:
        raise ValidationError(
            user_message="A stack needs at least two branches",
            beginner_help="One pull request on its own isn't a stack",
            common_fixes=["Deploy a conversion with more than one domain"],
            docs_anchor="github-integration",
            technical_message=f"Got {len(branches)} branch(es)",
        )

    result = link_existing_stack(
        branches,
        config.github.repository,
        base_branch=(request.json or {}).get("base_branch") or config.github.default_branch,
    )

    if not result["success"]:
        raise ConfigurationError(
            user_message=f"Could not link the pull requests into a stack: {result.get('reason', '')}",
            beginner_help=("Linking needs the GitHub CLI with the gh-stack extension, authenticated with GH_TOKEN"),
            common_fixes=[
                "Launch with GH_TOKEN=$(gh auth token) docker-compose up --build",
                "Check /api/github/status to see what's missing",
                "Link them yourself: gh stack link " + " ".join(branches),
            ],
            docs_anchor="github-integration",
            technical_message=str(result.get("reason")),
        )

    return jsonify(result)


def _selection_for(query, user_mart_selection):
    """The part of a conversion-wide mart selection that this query actually creates."""
    if user_mart_selection is None:
        return None

    from dbt_training_wheels.utils.sql_parser import extract_destination_datasets

    created = set(extract_destination_datasets(query.get("sql", "")))
    return [table for table in user_mart_selection if table in created]


def _build_stack_entries(
    conversion,
    config,
    query_config_service,
    commit_message: str,
    project_name: str,
    user_mart_selection,
    model_group_hint: str,
):
    """One entry per domain across the whole conversion, in deploy order.

    Two things can put a model in its own domain: the subfolder it was uploaded from,
    and a configured dataset mapping that overrides it. Both end up here as entries,
    so there's a single path to the stack rather than one per source.
    """
    from dbt_training_wheels.services.domain_resolver import (
        domain_from_filename,
    )
    from dbt_training_wheels.services.github_service import StackEntry

    entries = []

    for sibling in conversion["queries"]:
        sibling_domain = domain_from_filename(sibling.get("filename"))
        sibling_config = query_config_service.load_config(sibling["id"])

        analysis = analyze_query(
            cast(QueryInput, sibling),
            config,
            project_name=project_name,
            user_mart_selection=_selection_for(sibling, user_mart_selection),
            allow_empty_selection=True,
        )

        files = generate_files_for_query(
            cast(QueryInput, sibling),
            analysis,
            config,
            project_name=project_name,
            query_config=sibling_config,
            domain_area=sibling_domain,
            model_group=(sibling_config.model_group if sibling_config else "") or model_group_hint,
            user_mart_selection=_selection_for(sibling, user_mart_selection),
        )

        layers = analysis.get("layerClassification", {}) or {}
        active_layers = [layer for layer in ("staging", "intermediate", "mart") if layers.get(layer)]

        groups = attribute_models_to_domains(
            dict(analysis),
            config,
            project_name=project_name,
            fallback_domain=sibling_domain,
            query_filename=sibling.get("filename"),
        )
        buckets = group_files_by_domain(files, groups, dict(analysis))

        for group, bucket in zip(groups, buckets):
            if not bucket:
                continue
            entries.append(
                StackEntry(
                    name=group.domain,
                    files=bucket,
                    commit_message=f"{commit_message} ({group.domain})",
                    domain_area=group.domain,
                    active_layers=active_layers,
                )
            )

    return entries


def _push_one_group(
    entries,
    conversion,
    config,
    branch_name: str,
    base_slug: str,
    commit_message: str,
    create_pr: bool,
    force: bool,
    base_branch: str,
    dbt_project_name: str,
    pr_title: str,
    pr_body: str,
):
    """Push one group of domains that feed off each other.

    A group with one domain is an ordinary pull request; a group with several is a
    stack, ordered so a domain reading another's table merges after it. Returns the
    result dict, or None when the group had no files.

    dbt_project_name empty means don't touch dbt_project.yml - that's how shipping
    source files skips the dbt wiring that generated models need.
    """
    from dbt_training_wheels.services.github_service import push_stack_to_github, push_to_github

    if not entries:
        return None

    # One domain is one pull request - no stack language for a chain of one
    if len(entries) == 1:
        entry = entries[0]
        result = push_to_github(
            files=entry.files,
            branch_name=branch_name,
            commit_message=commit_message,
            config=config.github,
            models_path="",
            create_pr=create_pr,
            pr_title=pr_title or f"{conversion['name']} ({entry.domain_area})",
            pr_body=pr_body or f"From {conversion['name']}.\n\nGenerated by DBT Training Wheels.",
            domain_area=entry.domain_area if dbt_project_name else "",
            dbt_project_name=dbt_project_name,
            active_layers=entry.active_layers,
            force=force,
            base_branch=base_branch,
        )
        result["is_stack"] = False
        result["domains"] = [entry.domain_area]
        return result

    result = push_stack_to_github(
        entries,
        base_slug=base_slug,
        config=config.github,
        dbt_project_name=dbt_project_name,
        create_pr=create_pr or config.github.auto_create_pr,
        force=force,
        base_branch=base_branch,
    )

    top = result["stack"][-1]
    result["branch"] = top["branch"]
    result["branch_url"] = top["branch_url"]
    result["files_pushed"] = sum(item["files_pushed"] for item in result["stack"])
    result["is_stack"] = True
    result["domains"] = [entry.domain_area for entry in entries]
    return result


def _push_conversion_in_groups(
    conversion,
    config,
    build_entries,
    branch_name: str,
    commit_message: str,
    create_pr: bool,
    force: bool,
    base_branch: str,
    dbt_project_name: str,
    pr_title: str = "",
    pr_body: str = "",
    nothing_to_push: str = "There's nothing to deploy for this conversion",
):
    """Split a conversion into groups that feed off each other, and push each one.

    build_entries(group_queries) supplies the files for each group, keeping the
    grouping and push mechanics separate from what is being pushed.

    Groups push in sequence and a later one can fail after earlier ones landed, so the
    failure paths name what already reached GitHub rather than implying nothing did.
    """
    from dbt_training_wheels.services.domain_resolver import domain_from_filename, group_related_queries
    from dbt_training_wheels.services.github_service import BranchesExistError, GitHubError, _slugify_branch_part

    queries = conversion["queries"]
    groups = group_related_queries(queries) if len(queries) > 1 else [queries]

    logger.info(
        f"[GitHub Push] Pushing conversion '{conversion['name']}': "
        f"{len(queries)} domain(s) in {len(groups)} independent group(s)"
    )

    branch_prefix = config.github.branch_prefix or ""
    base_slug = (
        branch_name[len(branch_prefix) :] if branch_prefix and branch_name.startswith(branch_prefix) else branch_name
    )

    results = []
    try:
        for group_queries in groups:
            # With one group the user's branch name is used as typed. With several,
            # each needs its own branch or the second would collide with the first.
            if len(groups) == 1:
                group_branch = branch_name
            else:
                domain = domain_from_filename(group_queries[0].get("filename"))
                group_branch = f"{branch_prefix}{_slugify_branch_part(base_slug)}--{_slugify_branch_part(domain)}"

            result = _push_one_group(
                build_entries(group_queries),
                conversion,
                config,
                branch_name=group_branch,
                base_slug=base_slug,
                commit_message=commit_message,
                create_pr=create_pr,
                force=force,
                base_branch=base_branch,
                dbt_project_name=dbt_project_name,
                pr_title=pr_title,
                pr_body=pr_body,
            )
            if result:
                results.append(result)
    except BranchesExistError as e:
        raise _branch_conflict_error(e, already_pushed=_pushed_summary(results)) from e
    except GitHubError as e:
        landed = _pushed_summary(results)
        raise ConfigurationError(
            user_message=f"GitHub push failed: {e.message}",
            beginner_help=(
                f"{landed} was pushed before this failed - those branches are still on GitHub"
                if landed
                else "There was an issue pushing to GitHub"
            ),
            common_fixes=[
                "Verify the repository name is correct (owner/repo)",
                "Make sure your SSH key has write access to the repository",
                "Check your network connection",
            ],
            docs_anchor="github-integration",
            technical_message=str(e),
        ) from e

    if not results:
        raise ValidationError(
            user_message=nothing_to_push,
            beginner_help="No files were produced for any domain in this conversion",
            common_fixes=[
                "Check the analysis step for errors",
                "Make sure your SQL creates at least one table",
            ],
            docs_anchor="github-integration",
            technical_message=f"No stack entries built for conversion {conversion['name']}",
        )

    # One group keeps the original response shape exactly, so the common case is
    # unchanged for anything reading it
    if len(results) == 1:
        return jsonify(results[0])

    first = results[0]
    return jsonify(
        {
            "success": True,
            "is_grouped": True,
            "groups": results,
            "branch": first["branch"],
            "branch_url": first["branch_url"],
            "base_branch": first.get("base_branch"),
            "files_pushed": sum(r["files_pushed"] for r in results),
            "is_stack": any(r["is_stack"] for r in results),
        }
    )


def _conversion_of(query, config):
    """The conversion a query belongs to, or a conversion of just this query."""
    from dbt_training_wheels.services.domain_resolver import domain_from_filename
    from dbt_training_wheels.services.query_service import get_conversion_for_query

    return get_conversion_for_query(query["id"], config) or {
        "name": domain_from_filename(query.get("filename")) or query["name"],
        "queries": [query],
    }


def _deploy_conversion(
    query,
    config,
    query_config_service,
    branch_name: str,
    commit_message: str,
    project_name: str,
    user_mart_selection,
    create_pr: bool,
    force: bool,
    base_branch: str,
    model_group: str,
    pr_title: str = "",
    pr_body: str = "",
):
    """Deploy the whole conversion this query belongs to, as generated dbt models.

    One uploaded folder is one deploy, but not necessarily one stack. Domains that
    feed off each other are pushed as a stack, in merge order. Domains that share
    nothing are pushed independently off the base branch, so unrelated work isn't
    made to queue behind itself.
    """
    conversion = _conversion_of(query, config)

    def build_entries(group_queries):
        return _build_stack_entries(
            {"name": conversion["name"], "queries": group_queries},
            config,
            query_config_service,
            commit_message=commit_message,
            project_name=project_name,
            user_mart_selection=user_mart_selection,
            model_group_hint=model_group or conversion["name"],
        )

    return _push_conversion_in_groups(
        conversion,
        config,
        build_entries,
        branch_name=branch_name,
        commit_message=commit_message,
        create_pr=create_pr,
        force=force,
        base_branch=base_branch,
        dbt_project_name=config.dbt_project_name or project_name or "",
        pr_title=pr_title or f"Add dbt models: {conversion['name']}",
        pr_body=pr_body,
    )


def _pushed_summary(results) -> str:
    """The domains already on GitHub when a later group failed, for the error message."""
    domains = [d for r in results for d in r.get("domains", [])]
    return ", ".join(domains)


@github_bp.route("/push-to-github/<int:query_id>", methods=["POST"])
@handle_route_errors
def push_to_github_endpoint(query_id):
    """
    Push generated dbt model files to a GitHub branch.

    Requires:
    - GitHub configured in dbt_training_wheels_config.yaml (github.enabled, github.repository)
    - SSH keys mounted in Docker container (-v ~/.ssh:/home/dbt_training_wheels/.ssh:ro)

    Request Body:
        {
            "branch_name": "feature/my-models",  # Required
            "commit_message": "Add dbt models",  # Optional
            "domain": "marketing",               # Optional - domain/business area
            "model_group": "customer_orders",     # Optional - unique model group name
            "create_pr": false,                  # Optional - create PR after push
            "pr_title": "Add marketing models"   # Optional - PR title
        }

    Returns:
        JSON response with:
        - branch_url: URL to view the branch on GitHub
        - commit_sha: The commit SHA
        - files_pushed: Number of files pushed
        - pull_request: PR info if create_pr was true
    """
    from dbt_training_wheels.services.query_config_service import QueryConfigService

    config = get_org_config()
    query_config_service = QueryConfigService(storage=_storage, config=config)
    query_config = query_config_service.load_config(query_id)
    logger.info(
        f"[GitHub Push] query_config loaded: {query_config is not None}, model_path: {query_config.model_path if query_config else 'N/A'}"
    )

    # Check if GitHub is configured
    if not config or not config.github or not config.github.enabled:
        raise ConfigurationError(
            user_message="GitHub integration is not configured",
            beginner_help="To push files to GitHub, you need to configure the GitHub settings and mount SSH keys",
            common_fixes=[
                "Add 'github' section to your dbt_training_wheels_config.yaml",
                "Set github.enabled: true",
                "Set github.repository: 'owner/repo'",
                "Mount SSH keys: -v ~/.ssh:/home/dbt_training_wheels/.ssh:ro",
            ],
            docs_anchor="github-integration",
            technical_message="GitHub config not enabled or missing",
        )

    # Get query
    query = get_query_by_id(query_id, config)
    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to push",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=["Try uploading your SQL file again", "Refresh the page and start from the beginning"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Validate request
    if not request.json:
        raise ValidationError.missing_field("request body")

    branch_name = request.json.get("branch_name", "").strip()
    if not branch_name:
        raise ValidationError(
            user_message="Please provide a branch name",
            beginner_help="A branch name is required to push files to GitHub",
            common_fixes=[
                "Enter a branch name like 'feature/my-models'",
                "Use descriptive names like 'dbt_training_wheels/marketing-models'",
            ],
            docs_anchor="github-integration",
            technical_message="branch_name is required",
        )

    # Get optional parameters
    commit_message = request.json.get("commit_message", f"Add dbt models from {query['name']}")
    # Domain is derived per sibling in _build_stack_entries - it's the folder each
    # query was uploaded from, never something the request supplies
    # Documentation generation requires a model group, and the deploy path falls back to
    # the query name - do the same here rather than raising an unhandled ValueError
    model_group = request.json.get("model_group", "").strip() or query["name"]
    create_pr = request.json.get("create_pr", False)
    # Replace branches left by a previous deploy of this conversion
    force_push = request.json.get("force_push", False)
    # Land the stack on a branch other than the repo default, if asked
    base_branch = request.json.get("base_branch", "").strip()
    pr_title = request.json.get("pr_title", "")
    pr_body = request.json.get("pr_body", "")
    project_name = request.json.get("project", "").strip()
    # None means "auto-select every table"; an empty list is rejected downstream, so
    # normalise here rather than 500-ing on a deploy that didn't send a selection
    user_mart_selection = request.json.get("user_mart_selection") or None

    # Log mart selection for debugging
    if user_mart_selection:
        logger.info(f"[GitHub Push] Using user mart selection: {len(user_mart_selection)} tables selected")
    else:
        logger.warning("[GitHub Push] No user_mart_selection provided - will auto-select all tables")

    # Deploy is always the conversion: one uploaded folder goes out together, as a
    # stack when it spans domains and as a single pull request when it doesn't
    return _deploy_conversion(
        query,
        config,
        query_config_service,
        branch_name=branch_name,
        commit_message=commit_message,
        project_name=project_name,
        user_mart_selection=user_mart_selection,
        create_pr=create_pr,
        force=force_push,
        base_branch=base_branch,
        model_group=model_group,
        pr_title=pr_title,
        pr_body=pr_body,
    )


@github_bp.route("/github/status")
def github_status():
    """
    Check if GitHub integration is configured and working.

    Returns:
        JSON with enabled status and repository info
    """
    config = get_org_config()

    if not config or not config.github or not config.github.enabled:
        return jsonify({"enabled": False, "message": "GitHub integration not configured"})

    # Pushing branches needs only the SSH key, but opening pull requests goes through
    # the GitHub API - report which of those this environment can actually do, so the
    # deploy step can say so before the user pushes rather than after
    from dbt_training_wheels.services.gh_stack_service import check_gh_stack

    capability = check_gh_stack()

    return jsonify(
        {
            "enabled": True,
            "repository": config.github.repository,
            "default_branch": config.github.default_branch,
            "branch_prefix": config.github.branch_prefix,
            "auto_create_pr": config.github.auto_create_pr,
            "auth_method": "ssh",  # All GitHub operations use SSH keys
            "pull_requests": {
                "available": capability.available,
                "reason": capability.reason,
                "gh_version": capability.version_string,
            },
        }
    )


@github_bp.route("/health")
def health_check():
    """
    Health check endpoint for monitoring.

    Returns:
        JSON response with health status and version
    """
    return jsonify({"status": "healthy", "version": "0.1.0", "timestamp": datetime.now().isoformat()})
