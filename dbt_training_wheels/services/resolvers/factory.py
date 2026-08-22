"""Factory for creating cross-project reference resolvers."""

from dbt_training_wheels.config_schema import CrossProjectConfig
from dbt_training_wheels.services.resolvers.base import CrossProjectResolver
from dbt_training_wheels.services.resolvers.dataset_resolver import DatasetResolver


def create_resolver(config: CrossProjectConfig) -> CrossProjectResolver:
    """Factory to create appropriate resolver based on config.

    Args:
        config: CrossProjectConfig specifying resolver type and settings

    Returns:
        CrossProjectResolver implementation based on config.resolver type

    Currently supported resolver types:
        - "dataset": DatasetResolver (MVP) - maps datasets to projects

    Future resolver types:
        - "manifest": ManifestResolver - parses manifest.json for exact matches
        - "hybrid": HybridResolver - combines dataset and manifest approaches
    """
    resolver_type = config.resolver

    if resolver_type == "dataset":
        return DatasetResolver(config)
    # Future: Add manifest and hybrid resolvers
    # elif resolver_type == "manifest":
    #     return ManifestResolver(config)
    # elif resolver_type == "hybrid":
    #     return HybridResolver(config)
    else:
        # Default to dataset resolver
        return DatasetResolver(config)
