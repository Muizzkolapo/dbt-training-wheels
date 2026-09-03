Miniature dbt projects used as reader fixtures. Committed (never fetched) so
tests are deterministic and offline. `jaffle_shop/` mirrors the structure and
conventions of dbt-labs' jaffle_shop_duckdb (Apache-2.0); SQL bodies are
placeholders (`select 1 as id`) because the reader never parses SQL contents.
