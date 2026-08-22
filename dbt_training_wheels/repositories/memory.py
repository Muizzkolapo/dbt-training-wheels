"""In-memory Repository implementations.

These implementations store data in memory, suitable for single-process
deployments and testing. For multi-process or persistent storage,
use file-based or database-backed implementations.
"""

from dbt_training_wheels.repositories.base import ModelConfig, ModelConfigRepository, Query, QueryRepository


class InMemoryQueryRepository(QueryRepository):
    """In-memory implementation of QueryRepository."""

    def __init__(self):
        self._queries: dict[int, Query] = {}
        self._next_id = 1

    def get(self, id: int) -> Query | None:
        return self._queries.get(id)

    def get_all(self) -> list[Query]:
        return list(self._queries.values())

    def get_by_name(self, name: str) -> Query | None:
        for query in self._queries.values():
            if query.name == name:
                return query
        return None

    def get_next_id(self) -> int:
        return self._next_id

    def save(self, entity: Query) -> Query:
        if entity.id == 0:
            # New entity, assign ID
            entity.id = self._next_id
            self._next_id += 1

        self._queries[entity.id] = entity
        return entity

    def delete(self, id: int) -> bool:
        if id in self._queries:
            del self._queries[id]
            return True
        return False

    def clear(self) -> None:
        self._queries.clear()
        self._next_id = 1


class InMemoryModelConfigRepository(ModelConfigRepository):
    """In-memory implementation of ModelConfigRepository."""

    def __init__(self):
        self._configs: dict[int, list[ModelConfig]] = {}  # query_id -> configs

    def get(self, id: int) -> ModelConfig | None:
        # ID is not directly used for ModelConfig; use get_by_query_id instead
        for configs in self._configs.values():
            for config in configs:
                if hash((config.query_id, config.table)) == id:
                    return config
        return None

    def get_all(self) -> list[ModelConfig]:
        all_configs = []
        for configs in self._configs.values():
            all_configs.extend(configs)
        return all_configs

    def get_by_query_id(self, query_id: int) -> list[ModelConfig]:
        return self._configs.get(query_id, [])

    def save(self, entity: ModelConfig) -> ModelConfig:
        if entity.query_id not in self._configs:
            self._configs[entity.query_id] = []

        # Update existing or add new
        configs = self._configs[entity.query_id]
        for i, existing in enumerate(configs):
            if existing.table == entity.table:
                configs[i] = entity
                return entity

        configs.append(entity)
        return entity

    def save_for_query(self, query_id: int, configs: list[ModelConfig]) -> list[ModelConfig]:
        self._configs[query_id] = configs
        return configs

    def delete(self, id: int) -> bool:
        # Not directly applicable; use delete_by_query_id
        return False

    def delete_by_query_id(self, query_id: int) -> int:
        if query_id in self._configs:
            count = len(self._configs[query_id])
            del self._configs[query_id]
            return count
        return 0

    def clear(self) -> None:
        self._configs.clear()
