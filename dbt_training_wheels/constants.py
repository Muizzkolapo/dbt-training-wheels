"""Central constants and configuration for dbt_training_wheels.

This module provides a single source of truth for layer types, naming conventions,
and other constants used throughout the codebase. This prevents:
- Typos in layer names ("staging" vs "stagging")
- Inconsistent naming across frontend/backend
- Missing layers in UI components
"""

from dataclasses import dataclass
from enum import Enum


class LayerType(str, Enum):
    """Enum for dbt layer types - single source of truth.

    Using str as base allows direct string comparison and JSON serialization.
    """

    STAGING = "staging"
    INTERMEDIATE = "intermediate"
    MART = "mart"


@dataclass(frozen=True)
class LayerConfig:
    """Configuration for a dbt layer."""

    layer_type: LayerType
    default_prefix: str
    default_folder: str
    description: str

    @property
    def name(self) -> str:
        """Get layer name as string."""
        return self.layer_type.value


# Layer hierarchy in order (staging -> intermediate -> mart)
# This defines the processing order and display order
LAYER_HIERARCHY: list[LayerConfig] = [
    LayerConfig(
        layer_type=LayerType.STAGING,
        default_prefix="stg__",
        default_folder="staging",
        description="External source access - CTEs with only external dependencies",
    ),
    LayerConfig(
        layer_type=LayerType.INTERMEDIATE,
        default_prefix="int__",
        default_folder="intermediate",
        description="Transformations - CTEs with internal CTE dependencies",
    ),
    LayerConfig(
        layer_type=LayerType.MART,
        default_prefix="mart__",
        default_folder="marts",
        description="Final presentation layer - user-selected output tables",
    ),
]


def get_layer_config(layer_type: LayerType | str) -> LayerConfig | None:
    """Get configuration for a specific layer.

    Args:
        layer_type: LayerType enum or string name

    Returns:
        LayerConfig for the layer, or None if not found
    """
    if isinstance(layer_type, str):
        layer_type = LayerType(layer_type)

    for config in LAYER_HIERARCHY:
        if config.layer_type == layer_type:
            return config
    return None


def get_all_layers() -> list[LayerType]:
    """Get all layer types in hierarchy order."""
    return [config.layer_type for config in LAYER_HIERARCHY]
