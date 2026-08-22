"""
Layer Classification Module

Standalone module for classifying models into staging, intermediate, and mart layers.
This module encapsulates all business logic for determining which layer a model belongs to.

Key Classification Rules:
- User-selected tables → Mart layer
- Non-selected tables → Intermediate layer
- CTEs selecting from multiple external sources → Staging layer
"""

import logging
from dataclasses import dataclass

from dbt_training_wheels.constants import LayerType

logger = logging.getLogger(__name__)


# Re-export LayerType as Layer for backward compatibility
Layer = LayerType


@dataclass
class ClassificationResult:
    """Result of layer classification for a model"""

    model_name: str
    assigned_layer: Layer
    reason: str = ""
    source: str = ""  # e.g., "user_selected", "user_excluded_from_mart", "auto_detected_reuse"


class LayerClassifier:
    """
    Classifies models into staging, intermediate, and mart layers.

    Rules:
    - User-selected tables → Mart layer (final business outputs)
    - Every mart table → Gets an intermediate model (contains transformation logic)
    - Non-selected tables → Intermediate layer
    - CTEs selecting from multiple external sources → Staging layer

    Usage:
        classifier = LayerClassifier()

        # Classify by user selection
        result = classifier.classify_by_user_selection(
            table_name="customers",
            user_mart_selection=["customers", "orders"],
            model_name="customers"
        )
        # Returns: ClassificationResult(assigned_layer="mart", source="user_selected")
    """

    def classify_by_user_selection(
        self,
        table_name: str,
        user_mart_selection: list[str],
        model_name: str = "",
    ) -> ClassificationResult:
        """
        Classify a table based on whether user selected it for mart layer.

        Args:
            table_name: Table name to check
            user_mart_selection: List of table names user selected for mart
            model_name: Optional model name for logging

        Returns:
            ClassificationResult with assigned layer
        """
        if table_name in user_mart_selection:
            layer = Layer.MART
            reason = "User selected this table as final output"
            source = "user_selected"
        else:
            layer = Layer.INTERMEDIATE
            reason = "Not selected by user for mart layer"
            source = "user_excluded_from_mart"

        logger.debug(f"[LayerClassifier] {model_name}: {reason} → {layer}")

        return ClassificationResult(
            model_name=model_name,
            assigned_layer=layer,
            reason=reason,
            source=source,
        )

    def classify_cte_by_sources(
        self, external_source_count: int, has_internal_sources: bool = False, model_name: str = ""
    ) -> ClassificationResult:
        """
        Classify a CTE based on external and internal source dependencies.

        Rule:
        - Has ONLY external sources (no internal CTEs) -> staging
        - Has ANY internal CTE references -> intermediate (even if also has external sources)
        """
        if external_source_count >= 1 and not has_internal_sources:
            layer = Layer.STAGING
            reason = "CTE selects only from external sources (no internal CTEs)"
            source = "external_only_cte"
        else:
            layer = Layer.INTERMEDIATE
            reason = "CTE references internal CTEs or has no external dependencies"
            source = "has_internal_deps"

        logger.debug(f"[LayerClassifier] {model_name}: {reason} → {layer}")

        return ClassificationResult(
            model_name=model_name,
            assigned_layer=layer,
            reason=reason,
            source=source,
        )

    def classify_mart_intermediate(self, model_name: str = "") -> ClassificationResult:
        """
        Classify an intermediate model for a mart table.
        In 2-layer architecture, every mart table gets an intermediate model.

        Args:
            model_name: Optional model name for logging

        Returns:
            ClassificationResult with INTERMEDIATE layer
        """
        logger.debug(f"[LayerClassifier] {model_name}: Intermediate model for mart table")

        return ClassificationResult(
            model_name=model_name,
            assigned_layer=Layer.INTERMEDIATE,
            reason="2-layer architecture: intermediate model contains transformation logic",
            source="two_layer_architecture",
        )
