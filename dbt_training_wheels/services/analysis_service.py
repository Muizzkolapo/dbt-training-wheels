"""Service for analyzing SQL queries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from dbt_training_wheels.exceptions import AnalysisError
from dbt_training_wheels.models.types import (
    AnalysisResult,
    QueryInput,
    TableDetectionResult,
)
from dbt_training_wheels.services.naming_resolver import NamingResolver
from dbt_training_wheels.storage import FileSystemStorage
from dbt_training_wheels.utils.naming import get_case_style_and_separator, normalize_identifier
from dbt_training_wheels.utils.sql_formatter import format_dbt_model, format_sql
from dbt_training_wheels.utils.sql_parser import (
    analyze_sql_content,
    detect_sql_type,
    extract_and_transform_sql_for_table,
    extract_cte_models,
    extract_declare_variables,
    extract_destination_datasets,
    extract_final_select_source,
    extract_sql_for_table,
    extract_standalone_select,
    find_conflicting_table_basenames,
    find_recreated_tables,
    transform_sql_with_sources,
)

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig


@dataclass
class ResolvedNamingConfig:
    """Resolved naming configuration for analysis.

    layer_folder_names is the single source of truth for folder names.
    Individual folder properties (staging_folder, etc.) are computed from this dict.
    """

    staging_model_prefix: str
    intermediate_model_prefix: str
    mart_model_prefix: str

    # Single source of truth for folder names
    layer_folder_names: dict[str, str]

    # Property getters for backward compatibility
    @property
    def staging_folder(self) -> str:
        """Get staging folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("staging", "staging")

    @property
    def intermediate_folder(self) -> str:
        """Get intermediate folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("intermediate", "intermediate")

    @property
    def marts_folder(self) -> str:
        """Get marts folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("marts", "marts")


@dataclass
class TableSelectionSplit:
    """Result of splitting tables by user mart selection."""

    user_selected_mart_tables: list[dict]
    non_selected_tables: list[dict]


def detect_tables_for_query(query: QueryInput, config: OrganizationConfig | None = None) -> TableDetectionResult:
    """
    Detect all CREATE/INSERT tables in a query and provide mart recommendations.

    This function is called AFTER the prerequisite checklist, when user clicks
    "Continue to Analysis". It analyzes the SQL to find all tables and provides
    smart recommendations about which should be mart tables.

    Args:
        query: Query dictionary containing SQL and metadata
        config: Optional OrganizationConfig for custom patterns

    Returns:
        Dictionary with:
        - detectedTables: List of table dicts with name, fullName, dataset, scs, recommended, reason
        - recommendations: Dict with mart/intermediate lists and reasoning
        - requiresSelection: Always True
        - minMartTables: Minimum tables required (default 1)
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    # Parse SQL to extract all CREATE/INSERT table references
    sql_content = query.get("sql", "")

    # Refuse before offering a selection built on ambiguous names - two different
    # tables sharing a short name would collapse into one model with the wrong SQL
    conflicts = find_conflicting_table_basenames(sql_content)
    if conflicts:
        raise AnalysisError.duplicate_table_names(conflicts)

    recreated = find_recreated_tables(sql_content)
    if recreated:
        raise AnalysisError.recreated_tables(recreated)

    # Extract full qualified table names
    insert_pattern = re.compile(r"INSERT\s+INTO\s+[`\"]?([a-zA-Z0-9_.-]+)[`\"]?", re.IGNORECASE)
    create_pattern = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+[`\"]?([a-zA-Z0-9_.-]+)[`\"]?", re.IGNORECASE
    )

    insert_tables = insert_pattern.findall(sql_content)
    create_tables = create_pattern.findall(sql_content)
    all_full_tables = insert_tables + create_tables

    # Deduplicate while preserving order
    seen = set()
    unique_full_tables = []
    for table in all_full_tables:
        if table not in seen:
            seen.add(table)
            unique_full_tables.append(table)

    detected_tables = []
    recommended_mart = []
    recommended_intermediate = []

    for full_table_name in unique_full_tables:
        # Parse: project.dataset.table_name
        parts = full_table_name.split(".")
        table_name = parts[-1] if parts else full_table_name
        dataset = parts[-2] if len(parts) >= 2 else None

        # Extract SQL for this table to calculate SCS
        table_sql = extract_sql_for_table(sql_content, table_name)
        scs = 0
        complexity = "low"

        if table_sql:
            from dbt_training_wheels.services.analysis_service import calculate_sql_complexity_score

            scs, _ = calculate_sql_complexity_score(table_sql)

            if scs >= 20:
                complexity = "very_high"
            elif scs >= 8:
                complexity = "high"
            elif scs >= 3:
                complexity = "medium"
            else:
                complexity = "low"

        # No default recommendations - user must explicitly select marts
        recommended = False
        reason = "Please select which tables should be marts"

        # Build table info
        table_info = {
            "name": table_name,
            "fullName": full_table_name,
            "dataset": dataset,
            "scs": round(scs, 2),
            "complexity": complexity,
            "recommended": recommended,
            "reason": reason,
        }

        detected_tables.append(table_info)

        if recommended:
            recommended_mart.append(table_name)
        else:
            recommended_intermediate.append(table_name)

    # Build recommendations summary (2-layer architecture)
    mart_count = len(recommended_mart)
    intermediate_count = len(recommended_intermediate)

    reasoning = f"{mart_count} table(s) appear to be final outputs. "
    if intermediate_count > 0:
        reasoning += f"{intermediate_count} table(s) in temporary datasets are likely intermediate models."
    else:
        reasoning += "All tables appear to be final outputs."

    logger.info(
        f"[Table Detection] Found {len(detected_tables)} tables: "
        f"{mart_count} recommended for mart, {intermediate_count} for intermediate"
    )

    return {
        "detectedTables": detected_tables,  # type: ignore[typeddict-item]
        "recommendations": {
            "mart": recommended_mart,
            "intermediate": recommended_intermediate,
            "reasoning": reasoning,
        },
        "requiresSelection": True,
        "minMartTables": 1,
    }


def _collect_scheduled_query_projects(config: OrganizationConfig | None) -> list[str]:
    """Collect scheduled query project names from all project configs.

    Args:
        config: Organization configuration

    Returns:
        List of unique scheduled query project names
    """
    import logging

    logger = logging.getLogger(__name__)
    scheduled_query_projects = []

    if config and config.projects:
        for _, proj_config in config.projects.items():
            if proj_config.dbt_config and hasattr(proj_config.dbt_config, "scheduled_query_projects"):
                scheduled_query_projects.extend(proj_config.dbt_config.scheduled_query_projects)

        scheduled_query_projects = list(set(scheduled_query_projects))
        if scheduled_query_projects:
            logger.info(
                f"[Scheduled Query Detection] Loaded {len(scheduled_query_projects)} "
                f"scheduled query projects: {scheduled_query_projects}"
            )

    return scheduled_query_projects


def _resolve_naming_configuration(
    config: OrganizationConfig | None,
    project_name: str | None,
    naming_override: dict | None,
) -> ResolvedNamingConfig:
    """Resolve naming configuration from override, config, or schema defaults.

    Precedence: naming_override > NamingResolver > schema defaults
    Handles {project} placeholder replacement in prefixes.

    Args:
        config: Organization configuration
        project_name: Optional project name for project-specific config
        naming_override: Optional pre-computed naming overrides

    Returns:
        Resolved naming configuration
    """
    import logging

    from dbt_training_wheels.config_schema import ModelNamingConfig

    logger = logging.getLogger(__name__)

    # Get schema defaults
    schema_defaults = ModelNamingConfig()

    if naming_override:
        logger.info("[Analysis] Using naming override from QueryConfiguration")
        staging_model_prefix = naming_override.get("staging_model_prefix", schema_defaults.staging_model_prefix)
        intermediate_model_prefix = naming_override.get(
            "intermediate_model_prefix", schema_defaults.intermediate_model_prefix
        )
        mart_model_prefix = naming_override.get("mart_model_prefix", schema_defaults.mart_model_prefix)
        intermediate_folder = naming_override.get("intermediate_folder", "intermediate")
        marts_folder = naming_override.get("marts_folder", "marts")
    elif config:
        logger.info("[Analysis] Resolving naming using NamingResolver")
        naming_resolver = NamingResolver(config)
        resolved_naming = naming_resolver.resolve(query_naming=None, project_name=project_name)

        staging_model_prefix = resolved_naming.staging_model_prefix
        intermediate_model_prefix = resolved_naming.intermediate_model_prefix
        mart_model_prefix = resolved_naming.mart_model_prefix
        intermediate_folder = resolved_naming.intermediate_folder
        marts_folder = resolved_naming.marts_folder

        # Replace {project} placeholder
        if project_name:
            if "{project}" in staging_model_prefix:
                staging_model_prefix = staging_model_prefix.replace("{project}", project_name)
            if "{project}" in intermediate_model_prefix:
                intermediate_model_prefix = intermediate_model_prefix.replace("{project}", project_name)
            if "{project}" in mart_model_prefix:
                mart_model_prefix = mart_model_prefix.replace("{project}", project_name)
    else:
        logger.warning("[Analysis] No config available, using schema defaults")
        staging_model_prefix = schema_defaults.staging_model_prefix
        intermediate_model_prefix = schema_defaults.intermediate_model_prefix
        mart_model_prefix = schema_defaults.mart_model_prefix
        intermediate_folder = "intermediate"
        marts_folder = "marts"

    staging_folder: str = (
        str(naming_override.get("staging_folder") or "staging")
        if naming_override
        else (resolved_naming.staging_folder if config else "staging")
    )

    # Build layer_folder_names dict as single source of truth
    layer_folder_names = {
        "staging": staging_folder,
        "intermediate": intermediate_folder,
        "marts": marts_folder,
    }

    return ResolvedNamingConfig(
        staging_model_prefix=staging_model_prefix,
        intermediate_model_prefix=intermediate_model_prefix,
        mart_model_prefix=mart_model_prefix,
        layer_folder_names=layer_folder_names,
    )


def _split_tables_by_selection(
    final_table_sqls: list[dict],
    user_mart_selection: list[str],
) -> TableSelectionSplit:
    """Split tables based on user's mart selection.

    User-selected tables → mart layer
    Non-selected tables → intermediate layer

    Args:
        final_table_sqls: List of table SQL dicts
        user_mart_selection: Table names selected for mart

    Returns:
        Split with user_selected_mart_tables and non_selected_tables
    """
    import logging

    logger = logging.getLogger(__name__)
    user_selected_mart_tables = []
    non_selected_tables = []

    for table_sql in final_table_sqls:
        table_name = table_sql["table"]
        if table_name in user_mart_selection:
            user_selected_mart_tables.append(table_sql)
            logger.debug(f"[Mart Selection] '{table_name}' → mart layer")
        else:
            non_selected_tables.append(table_sql)
            logger.debug(f"[Mart Selection] '{table_name}' → intermediate layer")

    logger.info(f"[Mart Selection] Split: {len(user_selected_mart_tables)} mart, {len(non_selected_tables)} non-mart")

    return TableSelectionSplit(
        user_selected_mart_tables=user_selected_mart_tables,
        non_selected_tables=non_selected_tables,
    )


def _normalize_component_names(
    final_table_sqls: list[dict],
    layer_classification: dict,
    config: OrganizationConfig | None,
    project_name: str | None,
) -> None:
    """Normalize names for UI/config consistency (mutates in place).

    Adds 'originalName' field if normalization changes the name.

    Args:
        final_table_sqls: List to normalize (mutated)
        layer_classification: Dict to normalize (mutated)
        config: Organization configuration
        project_name: Optional project name
    """
    case_style, separator = get_case_style_and_separator(config, project_name)

    def _normalize_name(value: str) -> str:
        return normalize_identifier(value, case_style=case_style, separator=separator)

    # Normalize table names
    for table_sql in final_table_sqls:
        original = table_sql.get("table", "")
        normalized = _normalize_name(original)
        if normalized and normalized != original:
            table_sql["originalTable"] = original
            table_sql["table"] = normalized

    # Normalize layer component names
    for layer in ("staging", "intermediate", "mart"):
        for component in layer_classification.get(layer, []):
            original = component.get("name", "")
            normalized = _normalize_name(original)
            if normalized and normalized != original:
                component["originalName"] = original
                component["name"] = normalized


def _apply_cte_ref_transforms(
    layer_classification: dict,
    naming_config: ResolvedNamingConfig,
    extra_valid_refs: set[str] | None = None,
) -> None:
    """Replace internal CTE references with ref() calls based on layer mapping.

    Uses unified sqlglot AST parsing for robust table reference replacement.
    Falls back to regex only if AST completely fails.

    Mutates layer_classification in place by adding/updating transformedSql.
    """
    import logging
    import re

    from dbt_training_wheels.utils.sqlglot_parser import replace_all_table_references_unified

    logger = logging.getLogger(__name__)

    # Build replacement map: table_name -> {{ ref('prefix__table_name') }}
    table_replacements: dict[str, str] = {}
    cte_layers: dict[str, tuple[str, str]] = {}

    for layer in ("staging", "intermediate"):
        for component in layer_classification.get(layer, []):
            name = component.get("name")
            match_name = component.get("originalName") or name
            if name and match_name:
                cte_layers[match_name] = (layer, name)
                prefix = (
                    naming_config.staging_model_prefix
                    if layer == "staging"
                    else naming_config.intermediate_model_prefix
                )
                table_replacements[match_name] = f"{{{{ ref('{prefix}{name}') }}}}"

    logger.info(
        f"[CTE Ref Transform] Built replacement map with {len(table_replacements)} tables: {list(table_replacements.keys())}"
    )

    if not table_replacements:
        return

    def _replace_refs_ast(sql: str, current_name: str) -> tuple[str, bool]:
        """Use unified AST-based replacement (robust)."""
        # Build replacements excluding the current table (no self-reference)
        replacements = {
            k: v
            for k, v in table_replacements.items()
            if k != current_name and cte_layers.get(k, (None, None))[1] != current_name
        }

        if not replacements:
            return sql, True

        # Use unified AST function with internal_replacements
        transformed, replacements_made, success = replace_all_table_references_unified(
            sql=sql, internal_replacements=replacements
        )

        if replacements_made:
            logger.info(f"[CTE Ref Transform AST] '{current_name}': {', '.join(replacements_made)}")

        return transformed, success

    def _replace_refs_regex(sql: str, current_name: str) -> str:
        """Fallback regex-based replacement."""
        transformed = sql
        replacements_made = []
        for cte_name, (cte_layer, ref_name) in cte_layers.items():
            if cte_name == current_name or ref_name == current_name:
                continue
            prefix = (
                naming_config.staging_model_prefix
                if cte_layer == "staging"
                else naming_config.intermediate_model_prefix
            )
            # Match fully qualified names (project.dataset.table) and short names
            # Using lookahead instead of \b at end (backtick is not a word char)
            escaped_name = re.escape(cte_name)
            pattern = rf"(\bFROM|\bJOIN)\s+`?(?:[a-zA-Z0-9_-]+\.)?(?:[a-zA-Z0-9_-]+\.)?{escaped_name}`?(?=\s|$|\))"
            replacement = f"\\1 {{{{ ref('{prefix}{ref_name}') }}}}"
            new_transformed = re.sub(pattern, replacement, transformed, flags=re.IGNORECASE)
            if new_transformed != transformed:
                replacements_made.append(f"{cte_name} → {prefix}{ref_name}")
                transformed = new_transformed
        if replacements_made:
            logger.info(f"[CTE Ref Transform Regex] '{current_name}': {', '.join(replacements_made)}")
        return transformed

    for layer in ("staging", "intermediate"):
        for component in layer_classification.get(layer, []):
            sql = component.get("transformedSql") or component.get("sql") or ""
            if not sql:
                continue
            current_name = component.get("originalName") or component.get("name", "")

            # Try AST-based replacement first (more robust)
            transformed, ast_success = _replace_refs_ast(sql, current_name)

            # If AST completely failed, try regex as emergency fallback
            if not ast_success:
                logger.warning(f"[CTE Ref Transform] AST failed for '{current_name}', using regex fallback")
                transformed = _replace_refs_regex(sql, current_name)

            component["transformedSql"] = transformed
            logger.debug(f"[CTE Ref Transform] Processed {layer}/{current_name} (SQL: {len(sql)} chars)")

    # Validate all refs after transformation
    _validate_ref_transforms(layer_classification, naming_config, extra_valid_refs)


def _validate_ref_transforms(
    layer_classification: dict,
    naming_config: ResolvedNamingConfig,
    extra_valid_refs: set[str] | None = None,
) -> None:
    """Validate that all ref() calls reference actual models with correct prefixes.

    Catches common bugs:
    - Missing prefixes (ref('table') instead of ref('int__table'))
    - Self-references
    - Broken references to non-existent models
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    # Build set of all valid model names - including models produced by sibling
    # queries in the same conversion set, which this query may legitimately ref()
    valid_model_names = set(extra_valid_refs or set())
    for layer, prefix in [
        ("staging", naming_config.staging_model_prefix),
        ("intermediate", naming_config.intermediate_model_prefix),
    ]:
        for component in layer_classification.get(layer, []):
            name = component.get("name")
            if name:
                valid_model_names.add(f"{prefix}{name}")

    logger.info(f"[Validation] Valid model names: {sorted(valid_model_names)}")

    issues_found = []

    for layer in ("staging", "intermediate"):
        for component in layer_classification.get(layer, []):
            current_name = component.get("name", "")
            current_model = f"{naming_config.intermediate_model_prefix}{current_name}"
            sql = component.get("transformedSql") or component.get("sql") or ""

            if not sql:
                continue

            # Extract all ref() calls
            refs = re.findall(r"\{\{\s*ref\(['\"]([^'\"]+)['\"]\)\s*\}\}", sql)

            for ref in refs:
                # Issue 1: Self-reference
                if ref == current_model:
                    issues_found.append(f"❌ Self-reference in {current_model}: ref('{ref}')")

                # Issue 2: Missing prefix (no __ in name)
                elif "__" not in ref:
                    issues_found.append(
                        f"❌ Missing prefix in {current_model}: ref('{ref}') - should be ref('int__{ref}') or ref('stg__{ref}')"
                    )

                # Issue 3: Reference to non-existent model
                elif ref not in valid_model_names:
                    issues_found.append(f"❌ Broken ref in {current_model}: ref('{ref}') - model doesn't exist")

            # Issue 4: Fully qualified table names still present (not converted)
            if re.search(r"`[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+`", sql):
                fully_qualified = re.findall(r"`([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)`", sql)
                issues_found.append(
                    f"⚠️  Fully qualified table names not converted in {current_model}: {fully_qualified}"
                )

    if issues_found:
        logger.error(f"[Validation] Found {len(issues_found)} issues in generated refs:")
        for issue in issues_found:
            logger.error(f"  {issue}")
        # Don't raise exception, just log - let user see the issues in generated files
    else:
        logger.info("[Validation] ✅ All ref() calls are valid")


def _fix_self_reference_prefixes(
    hardcoded_tables: list[dict],
    layer_classification: dict[str, list[dict]],
    user_selected_mart_tables: list[dict],
    naming_config: ResolvedNamingConfig,
) -> None:
    """Fix suggestedRef for self-references based on actual layer classification.

    Self-references need to use the correct prefix based on which layer the
    referenced table was classified into (staging, intermediate, or mart).

    Args:
        hardcoded_tables: List of hardcoded table info (mutated)
        layer_classification: Dict with 'staging', 'intermediate', 'mart' lists
        user_selected_mart_tables: List of mart table SQL dicts
        naming_config: Resolved naming configuration
    """
    import logging

    logger = logging.getLogger(__name__)

    # Build lookup maps for each layer
    staging_table_names = {c.get("name", "").lower() for c in layer_classification.get("staging", [])}
    intermediate_table_names = {c.get("name", "").lower() for c in layer_classification.get("intermediate", [])}
    mart_table_names = {t.get("table", "").lower() for t in user_selected_mart_tables}

    logger.info(
        f"[Self-Ref Fix] Layer lookup - staging: {len(staging_table_names)}, "
        f"intermediate: {len(intermediate_table_names)}, mart: {len(mart_table_names)}"
    )

    for table in hardcoded_tables:
        if not table.get("isSelfReference"):
            continue

        full_table_ref = table.get("table", "")
        # Extract just the table name
        table_name = full_table_ref.split(".")[-1]
        table_name_lower = table_name.lower()

        # Check which layer this table belongs to
        if table_name_lower in mart_table_names:
            # Mart model - use mart prefix (or no prefix if empty)
            if naming_config.mart_model_prefix:
                ref_target = f"{naming_config.mart_model_prefix}{table_name}"
            else:
                ref_target = table_name
            table["suggestedRef"] = f"{{{{ ref('{ref_target}') }}}}"
            logger.debug(f"[Self-Ref Fix] '{table_name}' -> mart ref: {ref_target}")
        elif table_name_lower in staging_table_names:
            # Staging model - use staging prefix
            ref_target = f"{naming_config.staging_model_prefix}{table_name}"
            table["suggestedRef"] = f"{{{{ ref('{ref_target}') }}}}"
            logger.debug(f"[Self-Ref Fix] '{table_name}' -> staging ref: {ref_target}")
        else:
            # Intermediate model (default) - use intermediate prefix
            ref_target = f"{naming_config.intermediate_model_prefix}{table_name}"
            table["suggestedRef"] = f"{{{{ ref('{ref_target}') }}}}"
            logger.debug(f"[Self-Ref Fix] '{table_name}' -> intermediate ref: {ref_target}")


def _process_table_metadata(
    hardcoded_tables: list[dict],
    scheduled_query_projects: list[str],
    cross_project_decisions: dict[str, dict],
) -> None:
    """Enrich hardcoded tables with scheduled query and cross-project metadata.

    Mutates hardcoded_tables in place by adding:
    - isScheduledQueryDependency, scheduledQueryProject
    - isCrossProjectRef, crossProjectProject, crossProjectModel
    - Updated suggestedSource for cross-project refs

    Args:
        hardcoded_tables: List to enrich (mutated)
        scheduled_query_projects: List of scheduled query project names
        cross_project_decisions: Dict mapping original_reference to decision
    """
    import logging

    logger = logging.getLogger(__name__)

    # Process each table once
    for table in hardcoded_tables:
        full_table_ref = table.get("table", "")
        parts = full_table_ref.replace("`", "").replace('"', "").split(".")

        # Extract GCP project name
        gcp_project = parts[0] if len(parts) >= 3 else ""

        # Check scheduled query detection
        # Only mark as scheduled query dependency if it's NOT a self-reference
        # (self-references are tables created in the same SQL file)
        is_self_ref = table.get("isSelfReference", False)
        if gcp_project and scheduled_query_projects and gcp_project in scheduled_query_projects and not is_self_ref:
            table["isScheduledQueryDependency"] = True
            table["scheduledQueryProject"] = gcp_project
            logger.info(f"[Scheduled Query] Marked {full_table_ref} as dependency from {gcp_project}")

        # Check cross-project ref decisions (never for self/sibling refs - they're
        # models in this conversion, and the no-decision branch would clobber their state)
        if cross_project_decisions and not is_self_ref:
            # Build lookup key (dataset.table format)
            lookup_key = f"{parts[-2]}.{parts[-1]}" if len(parts) >= 2 else (parts[-1] if parts else "")

            decision = cross_project_decisions.get(lookup_key)
            if decision and decision.get("use_cross_ref"):
                project = decision.get("project", "")
                model = decision.get("model", "")
                if project and model:
                    table["suggestedSource"] = f"{{{{ ref('{project}', '{model}') }}}}"
                    table["isCrossProjectRef"] = True
                    table["crossProjectProject"] = project
                    table["crossProjectModel"] = model
            else:
                table["isCrossProjectRef"] = False
                table.pop("crossProjectProject", None)
                table.pop("crossProjectModel", None)
                table["suggestedSource"] = decision.get("suggested_source", "") if decision else ""


def _extract_final_table_sqls(
    query: QueryInput,
    sql_type: str,
    hardcoded_tables: list[dict],
    cross_project_decisions: dict[str, dict],
    dialect: str,
) -> list[dict]:
    """Extract and format SQL for each final table.

    Handles:
    1. Explicit table names (CREATE/INSERT statements)
    2. Standalone SELECT or WITH...SELECT queries

    For each table, extracts both original and transformed SQL.

    Args:
        query: Query input
        sql_type: Detected SQL type
        hardcoded_tables: Hardcoded table references
        cross_project_decisions: Cross-project ref decisions
        dialect: SQL dialect for formatting

    Returns:
        List of dicts with keys: table, sql, original_sql

    Raises:
        AnalysisError: If extraction fails
    """
    import logging

    logger = logging.getLogger(__name__)
    final_table_sqls = []

    # Extract DECLARE variables once
    full_sql_declare_variables = extract_declare_variables(query["sql"])

    # Destination datasets, kept for domain attribution (table names are short from here on)
    destinations = extract_destination_datasets(query["sql"])

    try:
        tables_to_process: list[str] | None = query.get("tables")  # type: ignore[assignment]

        # Auto-detect tables if not provided (backward compatibility)
        if not tables_to_process:
            import re

            # Extract table names from CREATE/INSERT statements
            create_pattern = re.compile(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+[`\"]?([a-zA-Z0-9_.-]+)[`\"]?", re.IGNORECASE
            )
            insert_pattern = re.compile(r"INSERT\s+INTO\s+[`\"]?([a-zA-Z0-9_.-]+)[`\"]?", re.IGNORECASE)

            create_tables = [m.split(".")[-1] for m in create_pattern.findall(query["sql"])]
            insert_tables = [m.split(".")[-1] for m in insert_pattern.findall(query["sql"])]
            tables_to_process = create_tables + insert_tables

            # Deduplicate while preserving order
            seen = set()
            unique_tables = []
            for table in tables_to_process:
                if table not in seen:
                    seen.add(table)
                    unique_tables.append(table)
            tables_to_process = unique_tables

            logger.info(f"[Table Extraction] Auto-detected {len(tables_to_process)} tables: {tables_to_process}")

        if tables_to_process:
            # Standard case: explicit table names or auto-detected
            for table in tables_to_process:
                # Extract ORIGINAL SQL (without transformation)
                original_sql_content = extract_sql_for_table(query["sql"], table)

                # Extract and TRANSFORM SQL
                sql_content = extract_and_transform_sql_for_table(
                    query["sql"],
                    table,
                    hardcoded_tables,
                    cross_project_decisions,
                    full_sql_declare_variables,
                )
                if sql_content:
                    formatted_original = (
                        format_sql(original_sql_content, dialect=dialect) if original_sql_content else ""
                    )
                    formatted_sql = format_dbt_model(sql_content, dialect=dialect)

                    # Extract CTEs specific to this table's INSERT statement
                    table_ctes = extract_cte_models(original_sql_content) if original_sql_content else []

                    # Extract the upstream CTE from the final SELECT
                    upstream_cte = extract_final_select_source(original_sql_content) if original_sql_content else None

                    logger.info(
                        f"[Per-Table CTE] Table '{table}': {len(table_ctes)} CTEs, "
                        f"upstream CTE from final SELECT: {upstream_cte}"
                    )

                    destination = destinations.get(table, {})

                    final_table_sqls.append(
                        {
                            "table": table,
                            "sql": formatted_sql,
                            "original_sql": formatted_original,
                            "ctes": table_ctes,  # CTEs specific to this INSERT
                            "upstreamCte": upstream_cte,  # CTE referenced in final SELECT
                            "dataset": destination.get("dataset", ""),  # For domain attribution
                            "fullName": destination.get("fullName", ""),
                        }
                    )
        elif sql_type in ("standalone_select", "with_cte"):
            # Standalone SELECT or WITH...SELECT
            standalone_sql = extract_standalone_select(query["sql"])
            if standalone_sql:
                original_standalone = standalone_sql
                # Transform with cross-project refs
                standalone_sql = transform_sql_with_sources(
                    standalone_sql,
                    hardcoded_tables,
                    cross_project_decisions,
                    full_sql_declare_variables,
                )
                model_name = query["name"].lower().replace(" ", "_").replace("-", "_")
                formatted_original = format_sql(original_standalone, dialect=dialect)
                formatted_sql = format_dbt_model(standalone_sql, dialect=dialect)

                # Extract CTEs specific to this statement
                table_ctes = extract_cte_models(original_standalone) if original_standalone else []
                upstream_cte = extract_final_select_source(original_standalone) if original_standalone else None

                final_table_sqls.append(
                    {
                        "table": model_name,
                        "sql": formatted_sql,
                        "original_sql": formatted_original,
                        "ctes": table_ctes,
                        "upstreamCte": upstream_cte,
                        # Standalone queries have no CREATE/INSERT target, so no dataset
                        "dataset": "",
                        "fullName": "",
                    }
                )
    except Exception as e:
        from dbt_training_wheels.exceptions.dbt_training_wheels_exceptions import DbtTrainingWheelsException

        if isinstance(e, DbtTrainingWheelsException):
            raise
        raise AnalysisError(
            user_message="We had trouble extracting table information from your query",
            beginner_help="This might be due to complex query structure or formatting",
            common_fixes=[
                "Try simplifying your SQL query",
                "Ensure table names are clearly defined",
                "Check that FROM and JOIN clauses are properly formatted",
            ],
            docs_anchor="analysis-errors",
            technical_message=f"Table extraction failed: {str(e)}",
        ) from e

    return final_table_sqls


def _assemble_analysis_results(
    query: QueryInput,
    models_count: int,
    sql_type: str,
    declare_variables: list[dict[str, Any]],
    prep_models: list[dict[str, Any]],
    hardcoded_tables: list[dict[str, Any]],
    final_table_sqls: list[dict[str, Any]],
    layer_classification: dict[str, Any],
    naming_config: ResolvedNamingConfig,
    project_name: str | None,
    case_style: str,
    separator: str,
) -> AnalysisResult:
    """Assemble the final analysis results dictionary.

    Pure data assembly - no business logic.

    Args:
        query: Query input
        models_count: Number of models to create
        sql_type: Detected SQL type
        declare_variables: DECLARE variables found
        prep_models: Prep/CTE models
        hardcoded_tables: Hardcoded table references
        final_table_sqls: Final table SQLs
        layer_classification: Layer classification results
        naming_config: Resolved naming configuration
        project_name: Optional project name
        case_style: Case style for naming
        separator: Separator for naming

    Returns:
        Complete analysis results dictionary
    """

    def _normalize_name(value: str) -> str:
        return normalize_identifier(value, case_style=case_style, separator=separator)

    return {
        "insertStatements": query.get("insertCount") or 1,
        "modelsToCreate": models_count,
        "sqlType": sql_type,
        "declareVariables": declare_variables,  # type: ignore[typeddict-item]
        "ctes": prep_models  # type: ignore[typeddict-item]
        if prep_models
        else [
            {
                "name": "No reused tables found",
                "canBeReused": False,
                "description": "No tables are created and reused within this script",
            }
        ],
        "hardcodedTables": hardcoded_tables if hardcoded_tables else [],  # type: ignore[typeddict-item]
        "suggestedPrep": len(prep_models),
        "suggestedFinal": models_count,
        "crossProjectRefs": [
            {
                "model": t["sourceTable"],
                "project": "other_project",
                "replaces": t["sourceTable"],
                "benefit": "Complete lineage visibility",
            }
            for t in hardcoded_tables
            if t.get("isDbtModel")
        ],
        "fileStructure": {
            "staging": [
                f"{naming_config.staging_model_prefix}{_normalize_name(p['name'])}.sql"
                for p in layer_classification.get("staging", [])
            ],
            "intermediate": [
                f"{naming_config.intermediate_model_prefix}{_normalize_name(p['name'])}.sql"
                for p in layer_classification.get("intermediate", [])
            ],
            "mart": [
                f"{naming_config.mart_model_prefix}{_normalize_name(p['name'])}.sql"
                for p in layer_classification.get("mart", [])
            ]
            if layer_classification.get("mart", [])
            else (
                [f"{naming_config.mart_model_prefix}{item['table']}.sql" for item in final_table_sqls]
                if final_table_sqls
                else [f"{naming_config.mart_model_prefix}{_normalize_name(query['name'])}.sql"]
            ),
        },
        "finalTableSqls": final_table_sqls,  # type: ignore[typeddict-item]
        "layerClassification": layer_classification,  # type: ignore[typeddict-item]
        "naming": {  # type: ignore[typeddict-unknown-key]
            "projectName": project_name,
            "stagingModelPrefix": naming_config.staging_model_prefix,
            "intermediateModelPrefix": naming_config.intermediate_model_prefix,
            "martModelPrefix": naming_config.mart_model_prefix,
            "stagingFolder": naming_config.staging_folder,
            "intermediateFolder": naming_config.intermediate_folder,
            "martsFolder": naming_config.marts_folder,
            "layerFolderNames": {
                "staging": naming_config.staging_folder,
                "intermediate": naming_config.intermediate_folder,
                "marts": naming_config.marts_folder,
            },
            "caseStyle": case_style,
            "separator": separator,
        },
    }


def analyze_conversion(
    conversion: dict[str, Any],
    config: OrganizationConfig | None = None,
    project_name: str | None = None,
    user_mart_selection: list[str] | None = None,
) -> AnalysisResult:
    """
    Analyze every domain of a conversion and merge the results into one view.

    A conversion is the unit of work, so the wizard sees all its domains at once: one
    layer classification, one set of tables, one lineage. Each component is tagged with
    the domain it belongs to, which is what lets the UI group without a second request.

    Cross-domain references need no special handling here - by the time analysis runs,
    sibling-reference detection has already turned them into ref() calls, so merging
    the components is enough for the lineage to connect.

    Args:
        conversion: Conversion dict from load_conversions(), with ordered 'queries'
        config: Organization configuration
        project_name: Project whose config applies
        user_mart_selection: Table names selected as marts across the whole conversion.
            Split per domain automatically - a domain with none selected gets none.

    Returns:
        A merged AnalysisResult covering every domain
    """
    from dbt_training_wheels.services.domain_resolver import domain_from_filename

    by_domain: dict[str, AnalysisResult] = {}

    for query in conversion.get("queries", []):
        domain = domain_from_filename(query.get("filename"))

        # Table names are unique across a conversion (duplicates are blocked at upload),
        # so the flat selection can be split by what each domain creates
        subset = None
        if user_mart_selection is not None:
            created = set(extract_destination_datasets(query.get("sql", "")))
            subset = [table for table in user_mart_selection if table in created]

        by_domain[domain] = analyze_query(
            cast(QueryInput, query),
            config,
            project_name=project_name,
            user_mart_selection=subset,
            allow_empty_selection=True,
        )

    return _merge_analysis_results(by_domain)


def _merge_analysis_results(by_domain: dict[str, AnalysisResult]) -> AnalysisResult:
    """Combine per-domain analyses into one, tagging every component with its domain.

    Deliberately does not keep the per-domain results alongside the merged one: the
    merged view contains everything, and analysis results are persisted to
    sessionStorage where duplicating them risks the quota.
    """
    if not by_domain:
        return cast(AnalysisResult, {})

    first = next(iter(by_domain.values()))
    merged: dict[str, Any] = {
        "insertStatements": 0,
        "modelsToCreate": 0,
        "sqlType": first.get("sqlType"),
        "declareVariables": [],
        "ctes": [],
        "hardcodedTables": [],
        "finalTableSqls": [],
        "crossProjectRefs": [],
        "layerClassification": {"staging": [], "intermediate": [], "mart": []},
        "fileStructure": {"staging": [], "intermediate": [], "mart": []},
        "naming": first.get("naming"),
        "domains": list(by_domain),
    }

    seen_tables: set[str] = set()

    for domain, result in by_domain.items():
        merged["insertStatements"] += result.get("insertStatements") or 0
        merged["modelsToCreate"] += result.get("modelsToCreate") or 0

        for layer in ("staging", "intermediate", "mart"):
            for component in (result.get("layerClassification") or {}).get(layer, []):
                merged["layerClassification"][layer].append({**component, "domain": domain})
            merged["fileStructure"][layer].extend((result.get("fileStructure") or {}).get(layer, []))

        for table in result.get("hardcodedTables") or []:
            key = str(table.get("table", ""))
            if key and key in seen_tables:
                continue
            seen_tables.add(key)
            merged["hardcodedTables"].append({**table, "domain": domain})

        for item in result.get("finalTableSqls") or []:
            merged["finalTableSqls"].append({**item, "domain": domain})

        merged["declareVariables"].extend(result.get("declareVariables") or [])
        merged["crossProjectRefs"].extend(result.get("crossProjectRefs") or [])

        # The placeholder "no reused tables" entry is noise once merged
        for cte in result.get("ctes") or []:
            if cte.get("canBeReused") is not False:
                merged["ctes"].append({**cte, "domain": domain})

    merged["suggestedPrep"] = len(merged["ctes"])
    merged["suggestedFinal"] = merged["modelsToCreate"]

    return cast(AnalysisResult, merged)


def analyze_query(
    query: QueryInput,
    config: OrganizationConfig | None = None,
    project_name: str | None = None,
    naming_override: dict[str, Any] | None = None,
    user_mart_selection: list[str] | None = None,
    allow_empty_selection: bool = False,
) -> AnalysisResult:
    """
    Analyze a SQL query and return detailed analysis results.

    IMPORTANT: user_mart_selection should be provided for accurate classification.
    Call detect_tables_for_query() first to show user the mart selection modal.

    Args:
        query: Query input containing SQL and metadata
        user_mart_selection: List of table names user identified as final mart outputs.
                           If not provided, auto-selects all tables (backward compatibility).
        config: Optional OrganizationConfig for customizing analysis
        project_name: Optional project name for project-specific configuration
        naming_override: Optional dict with pre-computed naming prefixes.
                        If provided, skips naming computation. Keys:
                        - intermediate_model_prefix, mart_model_prefix
                        - intermediate_folder, marts_folder

    Returns:
        Analysis results with detected tables, CTEs, and file structure
    """
    import logging

    logger = logging.getLogger(__name__)

    # Backward compatibility: if no user_mart_selection provided, auto-select all tables
    if user_mart_selection is None:
        logger.warning(
            "[Analysis] No user_mart_selection provided - auto-selecting all tables for mart. "
            "This is for backward compatibility. New UI flows should use detect_tables_for_query() first."
        )
        # Will be populated after we parse the query
        auto_select_all = True
    else:
        auto_select_all = False
        if not user_mart_selection and not allow_empty_selection:
            raise ValueError(
                "user_mart_selection cannot be empty. Must contain at least 1 table. "
                "Call detect_tables_for_query() first to get table recommendations."
            )
        logger.info(f"[Analysis] Starting analysis with {len(user_mart_selection)} user-selected mart tables")

    # Refuse ambiguous scripts up front: two different tables sharing a short name
    # would collapse into one model built from whichever statement comes first
    conflicts = find_conflicting_table_basenames(query.get("sql", ""))
    if conflicts:
        raise AnalysisError.duplicate_table_names(conflicts)

    recreated = find_recreated_tables(query.get("sql", ""))
    if recreated:
        raise AnalysisError.recreated_tables(recreated)

    # Resolve naming using NamingResolver (2-layer architecture)
    naming_config = _resolve_naming_configuration(config, project_name, naming_override)

    # Load cross-project ref decisions (if any have been saved)
    query_id = query.get("id")
    cross_project_decisions = (
        _load_cross_project_decisions(int(query_id) if isinstance(query_id, int | str) else 0) if query_id else {}
    )

    # Tables created by sibling queries in the same uploaded folder become ref() calls,
    # not source() calls - they're models in the same dbt project
    from dbt_training_wheels.services.query_service import get_sibling_created_tables

    sibling_tables = get_sibling_created_tables(dict(query)) if query.get("filename") else set()  # type: ignore[typeddict-item]
    if sibling_tables:
        logger.info(f"[Analysis] Sibling queries create {len(sibling_tables)} table(s): {sorted(sibling_tables)}")

    # Analyze the SQL content
    try:
        prep_models, hardcoded_tables = analyze_sql_content(
            query["sql"], config, project_name=project_name, sibling_tables=sibling_tables
        )
    except Exception as e:
        # If it's already a DbtTrainingWheelsException, re-raise it
        from dbt_training_wheels.exceptions.dbt_training_wheels_exceptions import DbtTrainingWheelsException

        if isinstance(e, DbtTrainingWheelsException):
            raise

        # Otherwise, convert to analysis error
        raise AnalysisError.table_extraction_failed() from e

    # ============================================================================
    # SCHEDULED QUERY DETECTION - Collect scheduled_query_projects from all projects
    # ============================================================================
    scheduled_query_projects = _collect_scheduled_query_projects(config)

    # Process table metadata (scheduled queries and cross-project refs)
    _process_table_metadata(hardcoded_tables, scheduled_query_projects, cross_project_decisions)

    # Extract CTE models with dependencies (for staging/intermediate classification)
    cte_models = extract_cte_models(query["sql"])
    if not cte_models:
        cte_models = prep_models
    elif prep_models:
        existing_names = {m.get("name") for m in cte_models}
        for prep in prep_models:
            if prep.get("name") not in existing_names:
                cte_models.append(prep)

    # Detect SQL type for proper handling
    try:
        sql_type = detect_sql_type(query["sql"])
    except Exception:
        # If detection fails, treat as unknown but continue
        sql_type = "unknown"

    # Get dialect for formatting
    dialect = "bigquery"
    if config and config.database:
        dialect = config.database.dialect

    # Extract SQL for each final table
    final_table_sqls = _extract_final_table_sqls(query, sql_type, hardcoded_tables, cross_project_decisions, dialect)

    # Determine the number of models to create
    insert_count = query.get("insertCount") or 1
    models_count = len(final_table_sqls) if final_table_sqls else max(1, insert_count)

    # Detect DECLARE variables - users must handle these manually
    declare_variables = extract_declare_variables(query["sql"])

    # Auto-select all tables if no user selection provided (backward compatibility)
    if auto_select_all:
        user_mart_selection = [t["table"] for t in final_table_sqls]
        logger.info(f"[Analysis] Auto-selected all {len(user_mart_selection)} tables for mart (backward compatibility)")

    # Split tables based on user's mart selection
    table_split = _split_tables_by_selection(final_table_sqls, user_mart_selection or [])
    user_selected_mart_tables = table_split.user_selected_mart_tables
    non_selected_tables = table_split.non_selected_tables

    logger.info(
        f"[Mart Selection] Split tables: {len(user_selected_mart_tables)} mart, "
        f"{len(non_selected_tables)} non-mart, {len(prep_models)} prep models"
    )

    # STEP 1: Initial classification to determine layers
    layer_classification = classify_ctes_by_layer(
        cte_models, user_selected_mart_tables, non_selected_tables, hardcoded_tables, declare_variables
    )

    # STEP 2: Extract SQL (WITHOUT fixing self-refs yet - will be handled by AST transform)
    final_table_sqls = _extract_final_table_sqls(query, sql_type, hardcoded_tables, cross_project_decisions, dialect)

    # STEP 3: Re-classify with final_table_sqls
    table_split = _split_tables_by_selection(final_table_sqls, user_mart_selection or [])
    user_selected_mart_tables = table_split.user_selected_mart_tables
    non_selected_tables = table_split.non_selected_tables

    layer_classification = classify_ctes_by_layer(
        cte_models, user_selected_mart_tables, non_selected_tables, hardcoded_tables, declare_variables
    )

    # STEP 4: NOW fix self-reference prefixes using FINAL layer classification
    # This must happen AFTER final classification but BEFORE we assemble results
    _fix_self_reference_prefixes(hardcoded_tables, layer_classification, user_selected_mart_tables, naming_config)

    # Normalize names for UI/config consistency
    _normalize_component_names(final_table_sqls, layer_classification, config, project_name)

    case_style, separator = get_case_style_and_separator(config, project_name)

    # Sibling models are valid ref() targets even though they're defined in another query
    sibling_ref_names = {
        match
        for table in hardcoded_tables
        if table.get("isSiblingReference")
        for match in re.findall(r"ref\('([^']+)'\)", str(table.get("suggestedRef", "")))
    }

    # Replace internal CTE references with ref() to staged/intermediate models
    _apply_cte_ref_transforms(layer_classification, naming_config, extra_valid_refs=sibling_ref_names)

    # Assemble final analysis results
    analysis_results = _assemble_analysis_results(
        query=query,
        models_count=models_count,
        sql_type=sql_type,
        declare_variables=declare_variables,
        prep_models=cte_models,
        hardcoded_tables=hardcoded_tables,
        final_table_sqls=final_table_sqls,
        layer_classification=layer_classification,
        naming_config=naming_config,
        project_name=project_name,
        case_style=case_style,
        separator=separator,
    )

    # Log metrics for visibility
    _log_analysis_metrics(analysis_results, hardcoded_tables)

    return analysis_results


def _log_analysis_metrics(analysis_results: AnalysisResult, hardcoded_tables: list[dict]) -> None:
    """Log analysis metrics for visibility and monitoring.

    Provides a clear summary of what was generated and helps spot anomalies.
    """
    import logging

    logger = logging.getLogger(__name__)

    layer_classification: dict = analysis_results.get("layerClassification", {})  # type: ignore[assignment]
    staging_count = len(layer_classification.get("staging", []))
    intermediate_count = len(layer_classification.get("intermediate", []))
    mart_count = len(layer_classification.get("mart", []))
    total_models = staging_count + intermediate_count + mart_count

    # Count references
    self_refs = sum(1 for t in hardcoded_tables if t.get("isSelfReference"))
    cross_project_refs = sum(1 for t in hardcoded_tables if t.get("isCrossProjectRef"))
    external_sources = len(hardcoded_tables) - self_refs - cross_project_refs

    # Count total ref() calls in generated SQL
    import re

    total_refs = 0
    for layer in ["staging", "intermediate"]:
        layer_components = layer_classification.get(layer, [])
        if not isinstance(layer_components, list):
            continue
        for comp in layer_components:
            sql = comp.get("transformedSql") or comp.get("sql") or ""
            total_refs += len(re.findall(r"\{\{\s*ref\(", sql))

    logger.info("\n" + "=" * 70)
    logger.info("📊 ANALYSIS METRICS")
    logger.info("=" * 70)
    logger.info("Models Generated:")
    logger.info(f"  • Total: {total_models} models")
    logger.info(f"  • Staging: {staging_count}")
    logger.info(f"  • Intermediate: {intermediate_count}")
    logger.info(f"  • Mart: {mart_count}")
    logger.info("")
    logger.info("References:")
    logger.info(f"  • Total ref() calls: {total_refs}")
    logger.info(f"  • Self-references: {self_refs} (internal tables)")
    logger.info(f"  • External sources: {external_sources}")
    logger.info(f"  • Cross-project refs: {cross_project_refs}")
    logger.info("")
    logger.info("Quality Checks:")
    if total_models == 0:
        logger.warning("  ⚠️  No models generated - check classification logic")
    if staging_count > intermediate_count * 2:
        logger.warning("  ⚠️  High staging count - verify classification rules")
    if self_refs > 0 and total_refs == 0:
        logger.warning("  ⚠️  Self-references detected but no ref() calls generated")
    if not any([staging_count > intermediate_count * 2, total_models == 0, self_refs > 0 and total_refs == 0]):
        logger.info("  ✅ All metrics look reasonable")
    logger.info("=" * 70 + "\n")


def _load_cross_project_decisions(query_id: int) -> dict[str, dict]:
    """Load cross-project ref decisions from storage.

    First tries to load from QueryConfiguration (preferred), then falls back
    to legacy temp file format.

    Args:
        query_id: Query identifier

    Returns:
        Dict mapping original_reference to decision dict
    """
    import json
    import logging

    logger = logging.getLogger(__name__)

    # Try QueryConfiguration first (preferred path)
    try:
        from dbt_training_wheels.config import get_org_config
        from dbt_training_wheels.services.query_config_service import QueryConfigService

        config = get_org_config()
        service = QueryConfigService(config=config)
        query_config = service.load_config(query_id)

        if query_config and query_config.cross_project_decisions:
            decisions = [d.to_dict() for d in query_config.cross_project_decisions]
            result = {d["original_reference"]: d for d in decisions if d.get("original_reference")}
            logger.info(f"[Analysis] Loaded {len(result)} cross-project decisions from QueryConfiguration")
            return result
    except Exception as e:
        logger.warning(f"[Analysis] Failed to load cross-project decisions from QueryConfiguration: {e}")

    # Fall back to legacy temp file
    storage = FileSystemStorage()
    filename = f"cross_project_refs_{query_id}.json"
    content = storage.read_temp_file(filename)

    if not content:
        return {}

    try:
        decisions = json.loads(content)
        # Convert list to lookup dict
        result = {d["original_reference"]: d for d in decisions if d.get("original_reference")}
        logger.info(f"[Analysis] Loaded {len(result)} cross-project decisions from legacy temp file")
        return result
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in cross-project refs file for query {query_id}")
        return {}


def calculate_sql_complexity_score(sql: str) -> tuple[int, dict]:
    """
    Calculate the SQL Complexity Score (SCS) for a SQL block.

        SCS = 1 + J + S + U + C + W + G + H + (window × 2) + distinct

    Where:
    - 1 = Base score (every component starts at 1)
    - J = JOINs (×1 each)
    - S = Subqueries (×1 each)
    - U = UNION/UNION ALL (×1 each)
    - C = CASE expressions (×1 each)
    - W = AND/OR conditions (×1 each)
    - G = GROUP BY (+1 if present)
    - H = HAVING (+1 if present)
    - window = Window functions (OVER) (×2 each - higher weight)
    - distinct = DISTINCT (+1 if present)

    Args:
        sql: SQL string to analyze

    Returns:
        Tuple of (scs_score, metrics_dict)
    """
    if not sql:
        return 1, {"base": 1}

    sql_upper = sql.upper()

    # Initialize metrics
    metrics = {
        "base": 1,
        "join_count": 0,
        "subquery_count": 0,
        "union_count": 0,
        "case_count": 0,
        "and_or_count": 0,
        "group_by": False,
        "having": False,
        "window_count": 0,
        "distinct": False,
    }

    # Count JOINs (all types)
    join_pattern = r"\b(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|JOIN)\b"
    metrics["join_count"] = len(re.findall(join_pattern, sql_upper, re.IGNORECASE))

    # Count subqueries (SELECT inside parentheses, excluding CTEs)
    # Simple heuristic: count SELECT statements that are not at the start
    subquery_pattern = r"\(\s*SELECT\b"
    metrics["subquery_count"] = len(re.findall(subquery_pattern, sql_upper, re.IGNORECASE))

    # Count UNION/UNION ALL
    union_pattern = r"\bUNION\s*(ALL)?\b"
    metrics["union_count"] = len(re.findall(union_pattern, sql_upper, re.IGNORECASE))

    # Count CASE expressions
    case_pattern = r"\bCASE\b"
    metrics["case_count"] = len(re.findall(case_pattern, sql_upper, re.IGNORECASE))

    # Count AND/OR conditions (in WHERE, ON, HAVING clauses)
    and_or_pattern = r"\b(AND|OR)\b"
    metrics["and_or_count"] = len(re.findall(and_or_pattern, sql_upper, re.IGNORECASE))

    # Check for GROUP BY
    if re.search(r"\bGROUP\s+BY\b", sql_upper, re.IGNORECASE):
        metrics["group_by"] = True

    # Check for HAVING
    if re.search(r"\bHAVING\b", sql_upper, re.IGNORECASE):
        metrics["having"] = True

    # Count window functions (OVER clause)
    window_pattern = r"\bOVER\s*\("
    metrics["window_count"] = len(re.findall(window_pattern, sql_upper, re.IGNORECASE))

    # Check for DISTINCT
    if re.search(r"\bDISTINCT\b", sql_upper, re.IGNORECASE):
        metrics["distinct"] = True

    # Calculate SCS
    scs = 1  # Base
    scs += metrics["join_count"]
    scs += metrics["subquery_count"]
    scs += metrics["union_count"]
    scs += metrics["case_count"]
    scs += metrics["and_or_count"]
    scs += 1 if metrics["group_by"] else 0
    scs += 1 if metrics["having"] else 0
    scs += metrics["window_count"] * 2  # Window functions have higher weight
    scs += 1 if metrics["distinct"] else 0

    return scs, metrics


def classify_ctes_by_layer(
    _prep_models: list[dict[str, Any]],  # Unused - kept for API compatibility
    user_selected_mart_tables: list[dict[str, Any]],
    non_selected_tables: list[dict[str, Any]] | None = None,
    hardcoded_tables: list[dict[str, Any]] | None = None,
    _declare_variables: list[dict[str, Any]] | None = None,  # Unused - kept for API compatibility
) -> dict[str, list[dict[str, Any]]]:
    """
    Classify tables into layers based on their FULL dependency chain.

    Classification Rules (based on lineage analysis):
    =================================================
    1. STAGING: A model is staging if EVERY table referenced in its full dependency
       chain (CTEs + base tables) is external/source. Pure "source -> light shaping".

    2. INTERMEDIATE: A model is intermediate if it references ANY internal models
       (other tables we're creating), or has mixed internal/external references.

    3. MART: User-selected final deliverable. This is a role-based designation that
       OVERRIDES structural classification. Not every file needs a mart.

    Key Implementation Details:
    - Build set of ALL internal tables (tables being created in our SQL)
    - For each target, traverse FULL dependency chain (CTEs + base tables)
    - Label each reference as internal (in our tables) or external
    - Classify: ALL external -> staging, ANY internal -> intermediate
    - Mart selection overrides structural classification

    Args:
        _prep_models: Unused - kept for API compatibility
        user_selected_mart_tables: List of table dicts user selected as final mart outputs
        non_selected_tables: List of table dicts user did NOT select (optional)
        hardcoded_tables: External table references for external source identification
        _declare_variables: Unused - kept for API compatibility

    Returns:
        Dict with keys 'staging', 'intermediate', 'mart', each containing a list
        of component dicts with name, sql, scs, metrics, layer, dependencies, source
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    if non_selected_tables is None:
        non_selected_tables = []
    if hardcoded_tables is None:
        hardcoded_tables = []

    # Build list of user-selected table names for classification
    user_mart_selection = [t.get("table", "") for t in user_selected_mart_tables]

    # Layer classification: STAGING (external only) + INTERMEDIATE (internal refs) + MART (user-selected)
    layer_classification: dict[str, list[dict[str, Any]]] = {
        "staging": [],
        "intermediate": [],
        "mart": [],
    }

    # =========================================================================
    # STEP 1: Build set of ALL INTERNAL tables (tables being created)
    # =========================================================================
    all_tables = user_selected_mart_tables + non_selected_tables
    internal_table_names: set[str] = set()
    for table_dict in all_tables:
        table_name = table_dict.get("table", "")
        if table_name:
            # Add both full name and short name
            internal_table_names.add(table_name.lower())
            internal_table_names.add(table_name.split(".")[-1].lower())

    logger.info(f"[Layer Classification] Internal tables (being created): {sorted(internal_table_names)}")

    # =========================================================================
    # STEP 2: Build external identifiers from hardcoded_tables
    # =========================================================================
    external_identifiers = set()
    for table in hardcoded_tables:
        if table.get("isSelfReference"):
            continue
        full_ref = table.get("table", "")
        full_ref = full_ref.replace("`", "").replace('"', "")
        if full_ref:
            external_identifiers.add(full_ref.lower())
            parts = full_ref.split(".")
            if len(parts) >= 2:
                external_identifiers.add(".".join(parts[-2:]).lower())
            external_identifiers.add(parts[-1].lower())
        source_table = table.get("sourceTable")
        if source_table:
            external_identifiers.add(str(source_table).lower())

    logger.info(f"[Layer Classification] External identifiers: {len(external_identifiers)} entries")

    # =========================================================================
    # STEP 3: Helper function to extract ALL base table references from SQL
    # =========================================================================
    def extract_base_table_refs(sql: str, cte_names: set[str]) -> tuple[set[str], set[str]]:
        """
        Extract all base table references from SQL, excluding CTE self-references.

        Returns:
            Tuple of (internal_refs, external_refs) - sets of table names
        """
        internal_refs: set[str] = set()
        external_refs: set[str] = set()

        # Find all FROM/JOIN references
        ref_pattern = r'(?:FROM|JOIN)\s+[`"]?([a-zA-Z0-9_.-]+)[`"]?'
        refs = re.findall(ref_pattern, sql, re.IGNORECASE)

        for ref in refs:
            ref_clean = ref.replace("`", "").replace('"', "").lower()
            ref_short = ref_clean.split(".")[-1]

            # Skip if it's a CTE within the same statement
            if ref_short in cte_names or ref_clean in cte_names:
                continue

            # Check if it's an internal table (one we're creating)
            if ref_short in internal_table_names or ref_clean in internal_table_names:
                internal_refs.add(ref_short)
            else:
                # It's external
                external_refs.add(ref_clean)

        return internal_refs, external_refs

    # =========================================================================
    # STEP 4: Helper function to classify based on dependency chain
    # =========================================================================
    def classify_by_dependencies(
        table_name: str,
        sql: str,
        ctes: list[dict],
        scs: int,
    ) -> str:
        """
        Classify a table based on its FULL dependency chain AND complexity (STRUCTURAL classification).

        Rules:
        - Staging: ALL external refs AND low complexity (SCS < 3) AND no CTEs
        - Intermediate: Complex transformations (SCS >= 3) OR has CTEs OR ANY internal refs

        Note: Mart selection is handled separately as a ROLE, not structural.
        """
        # Build set of CTE names for this table (to exclude from ref detection)
        cte_names = {cte.get("name", "").lower() for cte in ctes}

        # Collect ALL references from the full SQL (includes CTEs)
        all_internal_refs: set[str] = set()
        all_external_refs: set[str] = set()

        # Check the main SQL
        internal, external = extract_base_table_refs(sql, cte_names)
        all_internal_refs.update(internal)
        all_external_refs.update(external)

        # Also check each CTE's SQL for base table refs
        for cte in ctes:
            cte_sql = cte.get("sql", "")
            if cte_sql:
                internal, external = extract_base_table_refs(cte_sql, cte_names)
                all_internal_refs.update(internal)
                all_external_refs.update(external)

        # Remove self-reference
        all_internal_refs.discard(table_name.lower())
        all_internal_refs.discard(table_name.split(".")[-1].lower())

        logger.info(
            f"[Layer Classification] '{table_name}': "
            f"internal_refs={sorted(all_internal_refs)}, external_refs={len(all_external_refs)}, "
            f"scs={scs}, has_ctes={len(ctes) > 0}"
        )

        # Classification logic:
        # 1. ANY internal references -> always intermediate
        if len(all_internal_refs) > 0:
            return "intermediate"

        # 2. Has CTEs -> intermediate (transformations are happening)
        if len(ctes) > 0:
            return "intermediate"

        # 3. High complexity -> intermediate (joins, aggregations, etc.)
        # Threshold: SCS < 3 means simple SELECT (base=1, maybe 1 JOIN or 1 WHERE)
        if scs >= 3:
            return "intermediate"

        # 4. All external, no CTEs, low complexity -> staging
        return "staging"

    # =========================================================================
    # STEP 5: Process ALL tables with dependency-based classification
    # =========================================================================
    # Key insight: Mart is a ROLE, not a structural classification.
    # When user selects a table as mart:
    #   1. The FULL SQL stays in its structural layer (staging or intermediate)
    #   2. A thin WRAPPER is added to mart that refs the structural model
    # This ensures consistency: mart is always SELECT * FROM ref('int__...')
    # =========================================================================
    logger.info(f"[Layer Classification] Processing {len(all_tables)} tables")

    for table_dict in all_tables:
        name = table_dict.get("table", "unknown")
        sql = table_dict.get("original_sql", "") or table_dict.get("sql", "")
        ctes = table_dict.get("ctes", [])

        scs, metrics = calculate_sql_complexity_score(sql)

        # Check if user selected this table as a mart
        is_mart_selected = name in user_mart_selection

        # STRUCTURAL classification based on dependency chain AND complexity (ignores mart selection)
        structural_layer = classify_by_dependencies(name, sql, ctes, scs)

        # Determine component type for structural layer
        if structural_layer == "staging":
            component_type = "staging_table"
        else:
            component_type = "intermediate_table"

        # Add to STRUCTURAL layer (staging or intermediate) with the full SQL
        structural_component = {
            "name": name,
            "sql": table_dict.get("sql", sql),  # Use transformed SQL if available
            "original_sql": sql,
            "scs": scs,
            "metrics": metrics,
            "layer": structural_layer,
            "dependencies": [],
            "type": component_type,
            "source": "dependency_chain_analysis",
            "ctes": ctes,  # Keep CTEs for reference
            "dataset": table_dict.get("dataset", ""),  # Destination dataset, for domain attribution
        }

        layer_classification[structural_layer].append(structural_component)
        logger.info(f"[Layer Classification] '{name}' -> {structural_layer} (structural)")

        # If user selected as mart, ALSO add a thin wrapper to mart layer
        if is_mart_selected:
            mart_component = {
                "name": name,
                "sql": None,  # Mart SQL will be generated as SELECT * FROM ref('int__name')
                "original_sql": None,
                "scs": 0,  # Simple select wrapper
                "metrics": {},
                "layer": "mart",
                "dependencies": [name],  # Depends on the intermediate/staging model
                "type": "final",
                "source": "user_selection",
                "structuralLayer": structural_layer,  # Track which layer has the actual SQL
                "dataset": table_dict.get("dataset", ""),  # Destination dataset, for domain attribution
            }
            layer_classification["mart"].append(mart_component)
            logger.info(f"[Layer Classification] '{name}' -> mart (user-selected, refs {structural_layer})")

    # =========================================================================
    # NOTE: CTEs stay INLINED within their parent table's SQL
    # =========================================================================
    # We do NOT extract CTEs as separate models. Each CREATE TABLE statement
    # keeps its own WITH clause (CTEs) intact. This prevents artificial
    # circular dependencies that occur when CTEs are extracted.
    #
    # The classification (staging/intermediate/mart) applies to the TABLE
    # being created, based on the FULL dependency chain of that table.
    # =========================================================================

    # Classification complete
    logger.info(
        f"[Layer Classification] Classification complete: "
        f"staging={len(layer_classification.get('staging', []))}, "
        f"intermediate={len(layer_classification.get('intermediate', []))})"
    )

    # Log summary
    logger.info(
        f"[Layer Classification] Summary: "
        f"staging={len(layer_classification['staging'])}, "
        f"intermediate={len(layer_classification['intermediate'])}, "
        f"mart={len(layer_classification['mart'])}"
    )

    # Log detailed breakdown
    for layer in ["staging", "intermediate", "mart"]:
        if layer_classification[layer]:
            names = [str(c.get("name", "")) for c in layer_classification[layer] if c.get("name")]
            logger.info(f"[Layer Classification] {layer}: {', '.join(names)}")

    return layer_classification
