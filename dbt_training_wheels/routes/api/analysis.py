"""Analysis API routes for DBT Training Wheels.

Endpoints for analyzing SQL queries and generating dbt model files.
"""

import logging
import os
from typing import cast

from flask import Blueprint, jsonify, request

from dbt_training_wheels.config import get_org_config
from dbt_training_wheels.exceptions import ConfigurationError, FileSystemError, ValidationError
from dbt_training_wheels.models.types import AnalysisResult, QueryInput
from dbt_training_wheels.services.analysis_service import analyze_conversion, analyze_query, detect_tables_for_query
from dbt_training_wheels.services.file_generator import generate_files_for_query
from dbt_training_wheels.services.query_config_service import QueryConfigService
from dbt_training_wheels.services.query_service import get_conversion_for_query, get_query_by_id, grouped_source_files
from dbt_training_wheels.storage import FileSystemStorage
from dbt_training_wheels.utils import (
    handle_route_errors,
    validate_domain_name,
    validate_materialization_type,
    validate_safe_path,
    validate_schema_name,
    validate_tags,
)

logger = logging.getLogger(__name__)

analysis_bp = Blueprint("analysis", __name__)

# Storage instance for model configs
_storage = FileSystemStorage()


def _analyze_whole_conversion(query_id, query, config, project_name, user_mart_selection):
    """Analyze the conversion this query belongs to, not just the query.

    One uploaded folder is one unit of work, so the wizard sees every domain at once.
    Falls back to the single query if the conversion can't be resolved.
    """
    conversion = get_conversion_for_query(query_id, config)
    if conversion and len(conversion["queries"]) > 1:
        logger.info(
            f"[Analyze] Analyzing conversion '{conversion['name']}' "
            f"({len(conversion['queries'])} domains: {', '.join(conversion['domains'])})"
        )
        return analyze_conversion(
            conversion, config, project_name=project_name, user_mart_selection=user_mart_selection
        )

    return analyze_query(
        cast(QueryInput, query), config, project_name=project_name, user_mart_selection=user_mart_selection
    )


@analysis_bp.route("/queries/<int:query_id>/detect-tables")
@handle_route_errors
def detect_tables_endpoint(query_id):
    """
    Detect all CREATE/INSERT tables in a query and provide mart recommendations.

    This endpoint is called AFTER the prerequisite checklist, when user clicks
    "Continue to Analysis". It returns all detected tables with recommendations
    for which should be mart tables.

    Args:
        query_id: The ID of the query to analyze

    Query Parameters:
        project_name: Optional project name for project-specific configuration

    Returns:
        JSON with:
        - detectedTables: List of tables with recommendations
        - recommendations: Suggested mart/staging split
        - requiresSelection: True (user must select)
        - minMartTables: Minimum required (1)
    """
    # Get query from storage
    query = get_query_by_id(query_id)
    if not query:
        return jsonify({"error": f"Query with ID {query_id} not found"}), 404

    # Get project name from query params (optional)
    project_name = request.args.get("project_name")

    # Get organization config
    config = get_org_config()

    logger.info(f"Detecting tables for query {query_id}")
    if project_name:
        logger.info(f"Using project-specific config: {project_name}")

    # Tables from every domain of the conversion, so marts are chosen once for the
    # whole unit of work rather than once per subfolder
    conversion = get_conversion_for_query(query_id, config)
    if conversion and len(conversion["queries"]) > 1:
        from dbt_training_wheels.services.domain_resolver import domain_from_filename

        detected = []
        for sibling in conversion["queries"]:
            domain = domain_from_filename(sibling["filename"])
            for table in detect_tables_for_query(cast(QueryInput, sibling), config)["detectedTables"]:
                detected.append({**table, "domain": domain})

        logger.info(f"Detected {len(detected)} tables across {len(conversion['queries'])} domains")
        return jsonify(
            {
                "detectedTables": detected,
                "recommendations": {"mart": [], "intermediate": [t["name"] for t in detected], "reasoning": ""},
                "requiresSelection": True,
                "minMartTables": 1,
                "domains": conversion["domains"],
            }
        )

    # Detect tables and get recommendations
    detection_results = detect_tables_for_query(cast(QueryInput, query), config)

    return jsonify(detection_results)


@analysis_bp.route("/analyze/<int:query_id>")
@handle_route_errors
def analyze_query_endpoint(query_id):
    """
    Analyze a specific query and return analysis results.

    Args:
        query_id: The ID of the query to analyze

    Query Parameters:
        project_name: Optional project name for project-specific configuration
        user_mart_selection: Comma-separated list of table names selected for mart (optional)

    Returns:
        JSON response with analysis results
    """
    from dbt_training_wheels.services.query_config_service import QueryConfigService

    config = get_org_config()
    query = get_query_by_id(query_id, config)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the SQL file you're trying to analyze",
            beginner_help="The file might have been deleted or the ID is incorrect",
            common_fixes=[
                "Try uploading your SQL file again",
                "Check that you completed the 'Upload Files' step",
                "Refresh the page and start from the beginning",
            ],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Get project name from query parameters
    project_name = request.args.get("project_name")

    # Get user mart selection from query parameters (optional - for backward compatibility)
    user_mart_selection_str = request.args.get("user_mart_selection")
    user_mart_selection = None
    if user_mart_selection_str:
        # Parse comma-separated list
        user_mart_selection = [t.strip() for t in user_mart_selection_str.split(",") if t.strip()]
        logger.info(f"User selected {len(user_mart_selection)} tables for mart layer")

    if project_name:
        logger.info(f"Analyzing query {query_id} with project-specific config: {project_name}")
    else:
        logger.info(f"Analyzing query {query_id} WITHOUT project_name - using defaults")

    # Try to load QueryConfiguration (contains saved user decisions from steps 1-5)
    query_config_service = QueryConfigService(storage=_storage, config=config)
    query_config = query_config_service.load_config(query_id)

    if query_config:
        logger.info(f"[Analyze] Loaded QueryConfiguration for query {query_id} - applying saved decisions")

        # Always compute fresh analysis with saved decisions applied
        # This ensures cross-project refs and other user decisions are reflected
        logger.info("[Analyze] Computing fresh analysis with saved naming and decisions")
        analysis_results = _analyze_whole_conversion(
            query_id,
            query,
            config,
            project_name or query_config.project_name,
            user_mart_selection,
        )

        # Update cached analysis results in QueryConfiguration
        query_config_service.update_analysis_results(query_id, analysis_results)
        logger.info("[Analyze] Updated cached analysis results in QueryConfiguration")
    else:
        # No saved configuration - compute fresh analysis
        logger.info("[Analyze] No QueryConfiguration found - computing fresh analysis")
        analysis_results = _analyze_whole_conversion(query_id, query, config, project_name, user_mart_selection)

    return jsonify(analysis_results)


@analysis_bp.route("/save-model-config/<int:query_id>", methods=["POST"])
@handle_route_errors
def save_model_config_endpoint(query_id):
    """
    Save model configuration (materialization, schema, tags) for a query.
    Stores in local temp file for later retrieval during file generation.

    Args:
        query_id: The ID of the query

    Request Body:
        {
            "models": [
                {
                    "table": "table_name",
                    "materialization": "table|view|incremental|ephemeral",
                    "schema": "schema_name",
                    "tags": ["tag1", "tag2"]
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
            common_fixes=["Try uploading your SQL file again", "Refresh the page and start from the beginning"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Validate request body
    if not request.json:
        raise ValidationError.missing_field("request body")

    model_configs = request.json.get("models", [])

    if not isinstance(model_configs, list):
        raise ValidationError(
            user_message="The configuration format is invalid",
            beginner_help="Model configurations must be provided as a list",
            common_fixes=[
                "Check that you're sending the correct data format",
                "Refresh the page and try again",
                "Contact support if the problem persists",
            ],
            docs_anchor="validation-errors",
            technical_message="Expected 'models' to be an array",
        )

    # Validate each model configuration
    for model_config in model_configs:
        # Validate materialization
        if "materialization" in model_config:
            validate_materialization_type(model_config["materialization"])

        # Validate schema
        if "schema" in model_config and model_config["schema"]:
            validate_schema_name(model_config["schema"])

        # Validate tags
        if "tags" in model_config:
            validate_tags(model_config["tags"])

    # Save using storage abstraction
    try:
        _storage.save_model_config(query_id, model_configs)
    except Exception as e:
        raise ConfigurationError(
            user_message="We couldn't save your configuration",
            beginner_help="This might be a server storage issue",
            common_fixes=[
                "Try saving the configuration again",
                "Check available disk space",
                "Contact support if the problem persists",
            ],
            docs_anchor="configuration-errors",
            technical_message=f"Failed to save config file: {str(e)}",
        ) from e

    return jsonify({"success": True, "message": "Model configuration saved", "models_count": len(model_configs)})


@analysis_bp.route("/grouped-source/<int:query_id>", methods=["GET"])
@handle_route_errors
def grouped_source_endpoint(query_id):
    """
    Return the uploaded folder laid out by group, for download.

    No conversion happens here - the SQL is returned exactly as uploaded. What's added
    is the grouping: which subfolders read each other's tables (so they have to run in
    order) and which share nothing (so they don't). That's useful on its own, whether or
    not the team ever moves this to dbt.

    Returns:
        [{'path', 'content'}], the same shape as /generate-files, so the caller zips it
        the same way. Includes a GROUPS.md explaining the split.
    """
    config = get_org_config()

    query = get_query_by_id(query_id, config)
    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to download",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=["Try uploading your SQL file again", "Refresh the page"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    conversion = get_conversion_for_query(query_id, config) or {
        "name": query["name"],
        "queries": [query],
    }

    files = grouped_source_files(conversion)
    if not files:
        raise ValidationError(
            user_message="There's nothing to download for this upload",
            beginner_help="No SQL was found in the uploaded folder",
            common_fixes=["Upload a folder containing .sql files"],
            docs_anchor="file-errors",
            technical_message=f"No source files for conversion {conversion.get('name')}",
        )

    return jsonify(files)


@analysis_bp.route("/generate-files/<int:query_id>", methods=["GET", "POST"])
@handle_route_errors
def generate_files_endpoint(query_id):
    """
    Generate dbt model files for a specific query.
    Retrieves user-configured settings from QueryConfiguration or legacy temp file.

    Args:
        query_id: The ID of the query to generate files for

    Query Parameters:
        project_name: Optional project name for project-specific configuration

    Returns:
        JSON response with generated file information
    """
    from dbt_training_wheels.services.query_config_service import QueryConfigService

    config = get_org_config()
    query = get_query_by_id(query_id, config)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to generate files for",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=["Try uploading your SQL file again", "Refresh the page and start from the beginning"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Get project name, domain area, and user mart selection from query parameters
    project_name = request.args.get("project_name")
    domain_area = request.args.get("domain_area")
    model_group = request.args.get("model_group")
    user_mart_selection_str = request.args.get("user_mart_selection")
    user_mart_selection = None
    if user_mart_selection_str:
        user_mart_selection = [t.strip() for t in user_mart_selection_str.split(",") if t.strip()]
        logger.info(f"User selected {len(user_mart_selection)} tables for mart layer")

    if project_name:
        logger.info(f"Generating files for query {query_id} with project-specific config: {project_name}")
    if domain_area:
        logger.info(f"Using domain area for file organisation: {domain_area}")
    if model_group:
        logger.info(f"Using model group for unique model reference: {model_group}")

    # Try to load QueryConfiguration (new centralized config)
    query_config_service = QueryConfigService(storage=_storage, config=config)
    query_config = query_config_service.load_config(query_id)

    if query_config:
        # Use QueryConfiguration (preferred - naming already computed)
        logger.info(f"Using QueryConfiguration for file generation (query {query_id})")

        # Get analysis data - use cached if available, otherwise compute
        analysis_data: AnalysisResult
        if query_config.analysis_results:
            analysis_data = cast(AnalysisResult, query_config.analysis_results)
        else:
            # Compute analysis with naming override from QueryConfiguration
            analysis_data = analyze_query(
                cast(QueryInput, query),
                config,
                project_name=project_name,
                naming_override=query_config.naming.to_dict(),
                user_mart_selection=user_mart_selection,
            )
            # Cache the analysis results
            query_config_service.update_analysis_results(query_id, analysis_data)

        files = generate_files_for_query(
            cast(QueryInput, query),
            analysis_data,
            config,
            project_name=project_name,
            query_config=query_config,
            domain_area=domain_area,
            model_group=model_group,
            user_mart_selection=user_mart_selection,
        )
    else:
        # Fall back to legacy path
        logger.info(f"Using legacy config path for file generation (query {query_id})")

        # Get analysis data to include in file generation
        analysis_data = analyze_query(
            cast(QueryInput, query), config, project_name=project_name, user_mart_selection=user_mart_selection
        )

        # Retrieve saved model configuration using storage abstraction
        model_configs = _storage.load_model_config(query_id)

        files = generate_files_for_query(
            cast(QueryInput, query),
            analysis_data,
            config,
            model_configs,
            project_name=project_name,
            domain_area=domain_area,
            model_group=model_group,
            user_mart_selection=user_mart_selection,
        )

    return jsonify(files)


@analysis_bp.route("/write-to-dbt-project/<int:query_id>", methods=["POST"])
@handle_route_errors
def write_to_dbt_project_endpoint(query_id):
    """
    Write generated dbt model files directly to the configured dbt project path.

    Validates that:
    1. dbt_project.project_path is configured
    2. The path exists
    3. The models directory exists or can be created

    Args:
        query_id: The ID of the query to generate files for

    Returns:
        JSON response with success status and files written
    """
    config = get_org_config()
    query = get_query_by_id(query_id, config)
    query_config_service = QueryConfigService(storage=_storage, config=config)
    query_config = query_config_service.load_config(query_id)

    if not query:
        raise FileSystemError(
            user_message="We couldn't find the query you're trying to generate files for",
            beginner_help="The query might have been deleted or the ID is incorrect",
            common_fixes=["Try uploading your SQL file again", "Refresh the page and start from the beginning"],
            docs_anchor="file-errors",
            technical_message=f"Query not found with ID: {query_id}",
        )

    # Get project name from request body
    project_name = None
    if request.json and request.json.get("project_name"):
        project_name = request.json.get("project_name").strip()
        logger.info(f"Writing files for query {query_id} with project-specific config: {project_name}")

    # Get project path from request body, or fall back to current working directory
    project_path = None
    if request.json and request.json.get("project_path"):
        project_path = request.json.get("project_path").strip()
        # Validate path doesn't contain traversal sequences
        validate_safe_path(project_path)

    # Fall back to current working directory if no project path provided
    if not project_path:
        project_path = os.getcwd()

    # Verify this is a dbt project (has dbt_project.yml)
    if not os.path.exists(os.path.join(project_path, "dbt_project.yml")):
        raise ConfigurationError(
            user_message=f"No dbt project found at: {project_path}",
            beginner_help="The selected path doesn't contain a dbt_project.yml file",
            common_fixes=[
                "Select a different dbt project from the dropdown",
                "Make sure dbt_project.yml exists in the selected folder",
                f"Current path: {project_path}",
            ],
            docs_anchor="configuration-errors",
            technical_message=f"No dbt_project.yml found in {project_path}",
        )

    # Get domain from request body (optional) and validate
    domain = None
    if request.json and request.json.get("domain"):
        domain = validate_domain_name(request.json.get("domain"))

    # Get model group from request body
    model_group = None
    if request.json and request.json.get("model_group"):
        model_group = request.json.get("model_group").strip()

    # Get user mart selection from request body
    user_mart_selection = None
    if request.json and request.json.get("user_mart_selection"):
        user_mart_selection = request.json.get("user_mart_selection")
        if isinstance(user_mart_selection, list):
            logger.info(f"User selected {len(user_mart_selection)} tables for mart layer")
        else:
            logger.warning("user_mart_selection provided but not a list, ignoring")
            user_mart_selection = None

    # Determine models path from query config or domain
    if query_config and query_config.model_path:
        # Use full model path from config (domain/model_group)
        models_path = f"models/{query_config.model_path}"
    elif domain:
        # Use models/{domain}/ when domain is specified but no config loaded
        models_path = f"models/{domain}"
    else:
        # Fall back to configured models_path
        models_path = config.dbt_project.models_path

    # Determine absolute models path
    if os.path.isabs(models_path):
        abs_models_path = models_path
    else:
        abs_models_path = os.path.join(project_path, models_path)

    # Validate or create models directory
    try:
        os.makedirs(abs_models_path, exist_ok=True)
    except PermissionError as err:
        raise FileSystemError.permission_denied(abs_models_path) from err
    except Exception as e:
        raise FileSystemError(
            user_message="We couldn't create the models directory",
            beginner_help="This might be a permissions issue",
            common_fixes=[
                f"Check you have write permissions to {project_path}",
                "Try running with appropriate permissions",
                "Verify the path is correct",
            ],
            docs_anchor="file-errors",
            technical_message=f"Failed to create directory {abs_models_path}: {str(e)}",
        ) from e

    # Get analysis data
    analysis_data = analyze_query(
        cast(QueryInput, query), config, project_name=project_name, user_mart_selection=user_mart_selection
    )

    # Retrieve saved model configuration using storage abstraction
    model_configs = _storage.load_model_config(query_id)

    # Generate files
    files = generate_files_for_query(
        cast(QueryInput, query),
        analysis_data,
        config,
        model_configs,
        project_name=project_name,
        domain_area=domain,
        model_group=model_group,
        user_mart_selection=user_mart_selection,
    )

    # Write files to dbt project
    written_files = []
    for file_info in files:
        file_path = file_info["path"]
        file_content = file_info["content"]

        # Strip 'models/' prefix from file path if present (to avoid double-nesting)
        if file_path.startswith("models/"):
            file_path = file_path[len("models/") :]

        # Build full path
        full_path = os.path.join(abs_models_path, file_path)

        # Create parent directory if needed
        parent_dir = os.path.dirname(full_path)
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            raise FileSystemError(
                user_message=f"We couldn't create directory for {file_path}",
                beginner_help="This might be a permissions issue",
                common_fixes=[f"Check you have write permissions to {parent_dir}", "Verify the path is correct"],
                docs_anchor="file-errors",
                technical_message=f"Failed to create directory {parent_dir}: {str(e)}",
            ) from e

        # Write file
        try:
            with open(full_path, "w") as f:
                f.write(file_content)
            written_files.append({"path": file_path, "full_path": full_path, "type": file_info["type"]})
        except PermissionError as err:
            raise FileSystemError.permission_denied(full_path) from err
        except Exception as e:
            raise FileSystemError(
                user_message=f"We couldn't write file {file_path}",
                beginner_help="This might be a permissions or disk space issue",
                common_fixes=[
                    "Check available disk space",
                    f"Verify you have write permissions to {full_path}",
                    "Try again",
                ],
                docs_anchor="file-errors",
                technical_message=f"Failed to write file {full_path}: {str(e)}",
            ) from e

    return jsonify(
        {
            "success": True,
            "message": f"Successfully wrote {len(written_files)} files to dbt project",
            "project_path": project_path,
            "models_path": abs_models_path,
            "files": written_files,
        }
    )
