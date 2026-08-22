"""Cross-project references API routes for DBT Training Wheels.

Endpoints for detecting and configuring cross-project references (dbt Mesh support).
"""

import logging
from typing import cast

from flask import Blueprint, jsonify, request

from dbt_training_wheels.config import get_org_config
from dbt_training_wheels.exceptions import FileSystemError, ValidationError
from dbt_training_wheels.models.types import QueryInput
from dbt_training_wheels.services.analysis_service import analyze_query
from dbt_training_wheels.services.cross_project_service import get_cross_project_service
from dbt_training_wheels.services.query_service import get_query_by_id
from dbt_training_wheels.utils import handle_route_errors

logger = logging.getLogger(__name__)

cross_project_refs_bp = Blueprint("cross_project_refs", __name__)


@cross_project_refs_bp.route("/cross-project-refs/<int:query_id>", methods=["POST"])
@handle_route_errors
def detect_cross_project_refs(query_id):
    """Detect cross-project references in a query's table references.

    Analyzes the tables referenced in a query and identifies which ones
    belong to other dbt projects based on configuration.

    Args:
        query_id: The ID of the query to analyze

    Returns:
        JSON response with:
            - cross_project_refs: List of detected cross-project references
            - sources: List of tables that should remain as sources
            - summary: Count summary
    """
    config = get_org_config()
    query = get_query_by_id(query_id, config)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to analyze",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=[
                "Try uploading your SQL file again",
                "Refresh the page and start from the beginning",
            ],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Get user mart selection from request body if provided (optional, for consistency)
    user_mart_selection = None
    if request.json and request.json.get("user_mart_selection"):
        user_mart_selection = request.json.get("user_mart_selection")
        if not isinstance(user_mart_selection, list):
            user_mart_selection = None

    # Analyze the query to get full table references (hardcodedTables)
    # This is needed because query["tables"] only contains output table names,
    # but we need the source table references for cross-project detection
    from typing import Any

    analysis_data = analyze_query(cast(QueryInput, query), config, user_mart_selection=user_mart_selection)
    hardcoded_tables = cast(list[dict[Any, Any]], analysis_data.get("hardcodedTables", []))

    service = get_cross_project_service(config)
    result = service.detect_cross_project_refs(query, hardcoded_tables)

    # Immediately save the detected refs as decisions for use in Step 3+
    # This creates a table reference mapping that will be used going forward
    decisions = []

    # Add cross-project refs
    for ref in result.get("cross_project_refs", []):
        decisions.append(
            {
                "original_reference": ref["original_reference"],
                "use_cross_ref": True,
                "project": ref["project"],
                "model": ref["model"],
                "dataset": ref["dataset"],
                "table": ref["table"],
                "suggested_source": ref["suggested_source"],
            }
        )

    # Add sources (tables that should remain as sources)
    for source in result.get("sources", []):
        decisions.append(
            {
                "original_reference": source["original_reference"],
                "use_cross_ref": False,
                "project": None,
                "model": None,
                "suggested_source": source.get("suggested_source", ""),
            }
        )
    # Save the mapping immediately
    service.save_decisions(query_id, decisions)
    logger.info(f"Saved {len(decisions)} table reference mappings for query {query_id}")

    return jsonify(result)


@cross_project_refs_bp.route("/cross-project-refs/<int:query_id>/config", methods=["POST"])
@handle_route_errors
def save_cross_project_decisions(query_id):
    """Save user decisions about cross-project references.

    Stores the user's choices about which table references should use
    cross-project ref() syntax vs source() syntax.

    Args:
        query_id: The ID of the query

    Request Body:
        {
            "decisions": [
                {
                    "original_reference": "dataset.table",
                    "use_cross_ref": true,
                    "project": "project_name",
                    "model": "model_name"
                }
            ]
        }

    Returns:
        JSON response with success status
    """
    config = get_org_config()
    query = get_query_by_id(query_id, config)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to configure",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=[
                "Try uploading your SQL file again",
                "Refresh the page and start from the beginning",
            ],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Validate request body
    if not request.json:
        raise ValidationError.missing_field("request body")

    decisions = request.json.get("decisions", [])

    if not isinstance(decisions, list):
        raise ValidationError(
            user_message="The configuration format is invalid",
            beginner_help="Decisions must be provided as a list",
            common_fixes=[
                "Check that you're sending the correct data format",
                "Refresh the page and try again",
            ],
            docs_anchor="validation-errors",
            technical_message="Expected 'decisions' to be an array",
        )

    # Validate each decision
    for decision in decisions:
        if "original_reference" not in decision:
            raise ValidationError.missing_field("original_reference in decision")

        if "use_cross_ref" not in decision:
            raise ValidationError.missing_field("use_cross_ref in decision")

        # If using cross-ref, project and model are required
        if decision.get("use_cross_ref"):
            if not decision.get("project"):
                raise ValidationError.missing_field("project (required when use_cross_ref is true)")
            if not decision.get("model"):
                raise ValidationError.missing_field("model (required when use_cross_ref is true)")

    # Save decisions
    service = get_cross_project_service(config)
    service.save_decisions(query_id, decisions)

    return jsonify(
        {
            "success": True,
            "message": "Cross-project reference decisions saved",
            "decisions_count": len(decisions),
        }
    )


@cross_project_refs_bp.route("/cross-project-refs/<int:query_id>/config", methods=["GET"])
@handle_route_errors
def get_cross_project_decisions(query_id):
    """Get saved cross-project reference decisions.

    Retrieves previously saved decisions about cross-project references.

    Args:
        query_id: The ID of the query

    Returns:
        JSON response with saved decisions or empty list
    """
    config = get_org_config()

    service = get_cross_project_service(config)
    decisions = service.load_decisions(query_id)

    logger.info(f"[DEBUG] Loading decisions for query {query_id}: {decisions}")

    return jsonify(
        {
            "query_id": query_id,
            "decisions": decisions or [],
        }
    )


@cross_project_refs_bp.route("/cross-project-refs/status", methods=["GET"])
@handle_route_errors
def get_cross_project_refs_status():
    """Get cross-project refs feature status and configuration.

    Returns whether the feature is enabled and which projects are configured.

    Returns:
        JSON response with:
            - enabled: Boolean indicating if feature is enabled
            - projects: List of configured project names
            - datasets: List of all datasets mapped to projects
            - public_models: Dict mapping project names to their public models
    """
    config = get_org_config()
    service = get_cross_project_service(config)

    # Scan for public models in configured projects
    public_models = service.scan_all_public_models()

    return jsonify(
        {
            "enabled": service.is_enabled,
            "projects": service.get_known_projects(),
            "datasets": service.get_known_datasets(),
            "public_models": public_models,
        }
    )
