"""Models API routes for DBT Training Wheels.

Endpoints for source preview and model management.
"""

import logging
import os
from typing import Any, cast

from flask import Blueprint, jsonify, request

from dbt_training_wheels.config import get_org_config, get_project_config
from dbt_training_wheels.exceptions import FileSystemError
from dbt_training_wheels.models.types import QueryInput
from dbt_training_wheels.services.analysis_service import analyze_query
from dbt_training_wheels.services.query_service import get_query_by_id
from dbt_training_wheels.utils import handle_route_errors

logger = logging.getLogger(__name__)

models_bp = Blueprint("models", __name__)


@models_bp.route("/preview-sources/<int:query_id>")
@handle_route_errors
def preview_sources_endpoint(query_id):
    """
    Generate a preview of sources.yml with existing sources filtered out.

    This endpoint is used by the frontend to show a real-time preview
    in Step 6 that excludes sources already defined in the dbt project.

    Args:
        query_id: The ID of the query to analyze

    Query params:
        project: Optional project name to use for GitHub base_path

    Returns:
        JSON response with sources.yml content
    """
    from dbt_training_wheels.config_schema import GitHubConfig
    from dbt_training_wheels.services.file_generator import generate_sources_yml, scan_existing_sources

    config = get_org_config()
    query = get_query_by_id(query_id, config)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to preview",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=["Try uploading your SQL file again", "Refresh the page and start from the beginning"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Get project name from query param for project-specific GitHub config
    project_name = request.args.get("project")

    # Get user mart selection from query params if provided (optional, for consistency)
    user_mart_selection_str = request.args.get("user_mart_selection")
    user_mart_selection = None
    if user_mart_selection_str:
        user_mart_selection = [t.strip() for t in user_mart_selection_str.split(",") if t.strip()]

    # Get analysis data
    analysis_data = analyze_query(
        cast(QueryInput, query), config, project_name=project_name, user_mart_selection=user_mart_selection
    )

    # Scan existing sources - from GitHub if enabled, otherwise from local dbt project
    existing_sources = set()
    models_path = config.dbt_project.models_path if config and config.dbt_project else "models"

    if config and config.defaults.dbt_config.github.enabled:
        # Use remote scanning from GitHub
        try:
            from dbt_training_wheels.services.github_service import GitHubService

            # Get project-specific config with base_path
            defaults_github = config.defaults.dbt_config.github
            base_path = ""

            if project_name:
                project_config = get_project_config(project_name)
                if project_config and project_config.get("github"):
                    base_path = project_config["github"].get("base_path", "")

            # Create GitHubConfig with project's base_path (uses SSH - no token needed)
            github_config = GitHubConfig(
                enabled=defaults_github.enabled,
                repository=defaults_github.repository,
                default_branch=defaults_github.default_branch,
                branch_prefix=defaults_github.branch_prefix,
                auto_create_pr=defaults_github.auto_create_pr,
                pr_title_prefix=defaults_github.pr_title_prefix,
                pr_labels=defaults_github.pr_labels,
                base_path=base_path,
            )

            github_service = GitHubService(github_config)
            existing_sources = github_service.scan_existing_sources_remote(models_path)
            logger.info(f"[GitHub] Remote scan found {len(existing_sources)} existing sources")
        except Exception as e:
            # If remote scanning fails, continue without filtering
            logger.warning(f"[GitHub] Remote scanning failed: {type(e).__name__}: {e}", exc_info=True)
    else:
        # Use local scanning from current working directory
        project_path = os.getcwd()
        if os.path.exists(os.path.join(project_path, "dbt_project.yml")):
            try:
                existing_sources = scan_existing_sources(project_path, models_path)
            except Exception as e:
                # If scanning fails, continue without filtering
                logger.warning(f"Local source scanning failed: {e}")

    # Generate filtered sources.yml
    sources_yml_content = generate_sources_yml(cast(dict[str, Any], analysis_data), existing_sources)

    return jsonify(
        {
            "success": True,
            "sources_yml": sources_yml_content,
            "existing_sources_count": len(existing_sources),
            "filtered": len(existing_sources) > 0,
        }
    )


@models_bp.route("/mart-documentation/<int:query_id>", methods=["POST"])
@handle_route_errors
def save_mart_documentation(query_id):
    """Save mart model descriptions (legacy endpoint, delegates to generalized handler)."""
    return _save_model_documentation(query_id, "mart")


@models_bp.route("/model-documentation/<int:query_id>/<layer_type>", methods=["POST"])
@handle_route_errors
def save_model_documentation(query_id, layer_type):
    """
    Save model descriptions for any layer type (staging, intermediate, mart).

    Args:
        query_id: The ID of the query
        layer_type: The layer type ('staging', 'intermediate', or 'mart')

    Request JSON:
        {
          "model_name_1": "Description text...",
          "model_name_2": "Description text..."
        }

    Returns:
        JSON response with success status
    """
    valid_layers = {"staging", "intermediate", "mart"}
    if layer_type not in valid_layers:
        return jsonify({"success": False, "error": f"Invalid layer type: {layer_type}"}), 400

    return _save_model_documentation(query_id, layer_type)


def _save_model_documentation(query_id: int, layer_type: str):
    """Internal handler for saving model descriptions for any layer type."""
    from typing import Literal, cast

    from dbt_training_wheels.models.query_configuration import ModelConfiguration
    from dbt_training_wheels.services.query_config_service import QueryConfigService

    data = request.json

    if not data or not isinstance(data, dict):
        return jsonify({"success": False, "error": "Invalid request data"}), 400

    # Default materializations per layer
    default_materializations: dict[str, Literal["view", "table", "incremental", "ephemeral"]] = {
        "staging": "view",
        "intermediate": "table",
        "mart": "table",
    }

    config = get_org_config()
    query_config_service = QueryConfigService(config)

    # Get existing query config
    query_config = query_config_service.load_config(query_id)

    if not query_config:
        logger.warning(
            f"[{layer_type.title()} Documentation] No query config found for query {query_id}, creating new one"
        )
        query = get_query_by_id(query_id, config)
        if not query:
            return jsonify({"success": False, "error": "Query not found"}), 404

        from dbt_training_wheels.models.query_configuration import QueryConfiguration

        query_config = QueryConfiguration(query_id=query_id, query_name=query.get("name", ""))

    # Update or create model configurations with descriptions
    for model_name, description in data.items():
        if not description or not description.strip():
            continue

        description = description.strip()

        existing_config = query_config.get_model_config(model_name)

        if existing_config:
            existing_config.description = description
            logger.info(f"[{layer_type.title()} Documentation] Updated description for '{model_name}'")
        else:
            new_config = ModelConfiguration(
                table=model_name,
                model_type=cast(Literal["staging", "intermediate", "mart"], layer_type),
                description=description,
                materialization=default_materializations.get(layer_type, "table"),
            )
            query_config.model_configurations.append(new_config)
            logger.info(f"[{layer_type.title()} Documentation] Created new config with description for '{model_name}'")

    query_config_service.save_config(query_config)

    logger.info(f"[{layer_type.title()} Documentation] Saved {len(data)} descriptions for query {query_id}")

    return jsonify({"success": True, "updated_models": len(data)})
