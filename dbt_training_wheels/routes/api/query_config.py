"""Query Configuration API routes.

Provides centralized state management for the conversion wizard.
All wizard decisions are persisted to a single QueryConfiguration.
"""

import logging

from flask import Blueprint, jsonify, request

from dbt_training_wheels.config import get_org_config
from dbt_training_wheels.services.query_config_service import QueryConfigService
from dbt_training_wheels.services.query_service import get_query_by_id
from dbt_training_wheels.storage import FileSystemStorage

logger = logging.getLogger(__name__)

query_config_bp = Blueprint("query_config", __name__)


def _get_service() -> QueryConfigService:
    """Get QueryConfigService instance with current config."""
    config = get_org_config()
    storage = FileSystemStorage()
    return QueryConfigService(storage=storage, config=config)


@query_config_bp.route("/query-config/<int:query_id>", methods=["GET"])
def get_query_config(query_id: int):
    """
    Get the current QueryConfiguration for a query.

    Returns:
        200: Full QueryConfiguration as JSON
        404: If configuration not found
    """
    service = _get_service()
    config = service.load_config(query_id)

    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                    "beginner_help": "You need to analyze the query first before accessing its configuration.",
                    "common_fixes": [
                        "Go to Step 1 and click 'Analyze Query'",
                        "Make sure you've selected the correct query",
                    ],
                }
            }
        ), 404

    return jsonify(config.to_dict())


@query_config_bp.route("/query-config/<int:query_id>", methods=["POST"])
def create_query_config(query_id: int):
    """
    Create or reset QueryConfiguration.

    This initializes the configuration with:
    - Computed naming prefixes (from YAML config + project_name)
    - Initial model configurations
    - Empty cross-project decisions

    Request Body:
        {
            "project_name": "analytics",
            "github_branch": "dbt_training_wheels/my-model",
            "dbt_project_path": "/path/to/dbt"
        }

    Returns:
        200: Created QueryConfiguration as JSON
        400: If query not found
    """
    # Get the query
    query = get_query_by_id(query_id)
    if not query:
        return jsonify(
            {
                "error": {
                    "user_message": f"Query {query_id} not found",
                    "beginner_help": "The query you're trying to configure doesn't exist.",
                    "common_fixes": ["Make sure you've uploaded a SQL file", "Refresh the page and try again"],
                }
            }
        ), 400

    # Get request data
    data = request.get_json() or {}
    project_name = data.get("project_name")
    domain_area = data.get("domain_area")
    model_group = data.get("model_group")
    github_branch = data.get("github_branch")
    dbt_project_path = data.get("dbt_project_path")

    service = _get_service()

    # Create new configuration
    config = service.create_config(
        query_id=query_id,
        query=query,
        project_name=project_name,
        domain_area=domain_area,
        model_group=model_group,
        github_branch=github_branch,
        dbt_project_path=dbt_project_path,
    )

    logger.info(f"Created QueryConfiguration for query {query_id}")

    return jsonify(config.to_dict())


@query_config_bp.route("/query-config/<int:query_id>", methods=["DELETE"])
def delete_query_config(query_id: int):
    """
    Delete QueryConfiguration for a query.

    Returns:
        200: Success message
        404: If configuration not found
    """
    service = _get_service()
    deleted = service.delete_config(query_id)

    if not deleted:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                }
            }
        ), 404

    return jsonify({"success": True, "message": f"Deleted configuration for query {query_id}"})


@query_config_bp.route("/query-config/<int:query_id>/step/<step_id>", methods=["PATCH"])
def update_step_config(query_id: int, step_id: str):
    """
    Update configuration for a specific step.

    step_id values:
        - "cross_project_refs": Update cross_project_decisions
        - "materialization": Update model_configurations.materialization
        - "schema": Update model_configurations.schema
        - "tags": Update model_configurations.tags
        - "completion": Update step_completion flags
        - "analysis": Update analysis_results

    Request Body varies by step_id:
        For "materialization"/"schema"/"tags":
            {"models": [{"table": "...", "materialization": "table"}]}

        For "cross_project_refs":
            {"decisions": [{"original_reference": "...", ...}]}

        For "completion":
            {"step": "analyze", "completed": true}

        For "analysis":
            {"analysis_results": {...}}

    Returns:
        200: Updated QueryConfiguration as JSON
        400: If invalid step_id or missing data
        404: If configuration not found
    """
    service = _get_service()
    data = request.get_json() or {}

    if step_id == "cross_project_refs":
        decisions = data.get("decisions", [])

        from dbt_training_wheels.config import get_org_config
        from dbt_training_wheels.services.cross_project_service import get_cross_project_service

        org_config = get_org_config()
        cross_service = get_cross_project_service(org_config)
        cross_service.save_decisions(query_id, decisions)

        config = service.load_config(query_id)
        if config:
            updated_config = service.update_cross_project_decisions(query_id, decisions)
            if updated_config:
                return jsonify(updated_config.to_dict())
        return jsonify({"success": True, "message": "Decisions saved"})

    config = service.load_config(query_id)
    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                    "beginner_help": "You need to analyze the query first.",
                }
            }
        ), 404

    if step_id in ("materialization", "schema", "tags"):
        models = data.get("models", [])
        if not models:
            return jsonify(
                {
                    "error": {
                        "user_message": "No models provided",
                        "beginner_help": f"The {step_id} update requires a 'models' array.",
                    }
                }
            ), 400
        config = service.update_model_configurations(query_id, models, step=step_id)

    elif step_id == "completion":
        step_name = data.get("step")
        completed = data.get("completed", True)
        if not step_name:
            return jsonify(
                {
                    "error": {
                        "user_message": "No step name provided",
                        "beginner_help": "The completion update requires a 'step' field.",
                    }
                }
            ), 400
        config = service.update_step_completion(query_id, step_name, completed)

    elif step_id == "analysis":
        analysis_results = data.get("analysis_results")
        if not analysis_results:
            return jsonify(
                {
                    "error": {
                        "user_message": "No analysis results provided",
                    }
                }
            ), 400
        config = service.update_analysis_results(query_id, analysis_results)

    else:
        return jsonify(
            {
                "error": {
                    "user_message": f"Unknown step: {step_id}",
                    "beginner_help": "Valid steps are: cross_project_refs, materialization, schema, tags, completion, analysis",
                }
            }
        ), 400

    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": "Failed to update configuration",
                }
            }
        ), 500

    return jsonify(config.to_dict())


@query_config_bp.route("/query-config/<int:query_id>/naming", methods=["GET"])
def get_naming_config(query_id: int):
    """
    Get just the naming configuration.

    Used by frontend to get prefixes without loading full config.

    Returns:
        200: Naming configuration as JSON
        404: If configuration not found
    """
    service = _get_service()
    config = service.load_config(query_id)

    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                }
            }
        ), 404

    return jsonify(config.naming.to_dict())


@query_config_bp.route("/query-config/<int:query_id>/models", methods=["GET"])
def get_model_configs(query_id: int):
    """
    Get model configurations as a lookup map.

    Returns:
        200: Model configurations as {table: config} map
        404: If configuration not found
    """
    service = _get_service()
    config = service.load_config(query_id)

    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                }
            }
        ), 404

    return jsonify(
        {
            "models": [m.to_dict() for m in config.model_configurations],
            "lookup": config.get_model_config_map(),
        }
    )


@query_config_bp.route("/query-config/<int:query_id>/cross-project-decisions", methods=["GET"])
def get_cross_project_decisions(query_id: int):
    """
    Get cross-project decisions as a lookup map.

    Returns:
        200: Cross-project decisions
        404: If configuration not found
    """
    service = _get_service()
    config = service.load_config(query_id)

    if not config:
        return jsonify(
            {
                "error": {
                    "user_message": f"No configuration found for query {query_id}",
                }
            }
        ), 404

    return jsonify(
        {
            "decisions": [d.to_dict() for d in config.cross_project_decisions],
            "lookup": config.get_cross_project_decisions_map(),
        }
    )
