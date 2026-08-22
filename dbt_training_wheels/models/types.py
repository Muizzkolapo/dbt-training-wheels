"""Type definitions for dbt_training_wheels data structures.

This module defines TypedDict classes for all dictionary structures passed between
services, improving type safety, IDE support, and maintainability.
"""

from typing import Any, TypedDict


class HardcodedTableInfo(TypedDict, total=False):
    """Type definition for hardcoded table information.

    These are tables referenced in the SQL that are not created within the script.
    """

    sourceTable: str
    table_name: str
    is_temp_table: bool
    db_project: str | None
    dataset: str | None
    schema: str | None
    isDbtModel: bool
    originalName: str | None
    name: str | None


class CteTableInfo(TypedDict, total=False):
    """Type definition for CTE (Common Table Expression) information."""

    name: str
    canBeReused: bool
    description: str
    sql: str | None
    dependencies: list[str] | None
    complexity_score: int | None
    originalName: str | None


class SourceTableInfo(TypedDict, total=False):
    """Type definition for source table information."""

    sourceTable: str
    normalized: str | None
    originalName: str | None


class FinalTableInfo(TypedDict, total=False):
    """Type definition for final table information."""

    table: str
    sql: str
    dependencies: list[str]
    complexity_score: int | None
    layer: str | None  # "staging", "intermediate", or "mart"
    originalName: str | None
    name: str | None


class LayerClassification(TypedDict, total=False):
    """Type definition for layer classification results.

    Each layer contains a list of component dicts with fields like:
    - name: Model name
    - sql: SQL content
    - transformedSql: Transformed SQL (after ref() replacement)
    - dependencies: List of dependencies
    - complexity_score: Complexity score
    - upstreamCte: Upstream CTE name (for mart models)
    - originalName: Original table/CTE name
    """

    staging: list[dict[str, Any]]
    intermediate: list[dict[str, Any]]
    mart: list[dict[str, Any]]
    reasoning: dict[str, str]


class FileStructure(TypedDict):
    """Type definition for file structure output."""

    staging: list[str]
    intermediate: list[str]
    mart: list[str]


class NamingInfo(TypedDict, total=False):
    """Type definition for naming configuration in analysis results."""

    projectName: str | None
    stagingModelPrefix: str
    intermediateModelPrefix: str
    martModelPrefix: str
    intermediateFolder: str
    martsFolder: str
    layerFolderNames: dict[str, str]
    caseStyle: str
    separator: str


class CrossProjectRef(TypedDict):
    """Type definition for cross-project reference."""

    model: str
    project: str
    replaces: str
    benefit: str


class TableRecommendations(TypedDict):
    """Type definition for table recommendations."""

    mart: list[str]
    intermediate: list[str]
    reasoning: str


class TableDetectionResult(TypedDict):
    """Type definition for table detection result."""

    detectedTables: list[str]
    recommendations: TableRecommendations
    requiresSelection: bool
    minMartTables: int


class AnalysisResult(TypedDict, total=False):
    """Type definition for analysis service output."""

    insertStatements: int
    modelsToCreate: int
    sqlType: str
    declareVariables: list[str]
    ctes: list[CteTableInfo]
    hardcodedTables: list[HardcodedTableInfo]
    suggestedPrep: int
    suggestedFinal: int
    crossProjectRefs: list[CrossProjectRef]
    fileStructure: FileStructure
    finalTableSqls: list[FinalTableInfo]
    layerClassification: LayerClassification
    naming: NamingInfo


class QueryInput(TypedDict, total=False):
    """Type definition for query input."""

    sql: str
    name: str
    target_folder: str
    description: str | None
    tags: list[str]
    insertCount: int | None


class GeneratedFile(TypedDict):
    """Type definition for generated file info."""

    path: str
    content: str
    model_type: str  # "intermediate" or "mart"
    model_name: str


class GenerationResult(TypedDict, total=False):
    """Type definition for file generation result."""

    files: list[GeneratedFile]
    schema_file: dict[str, Any] | None
    error: str | None
