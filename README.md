# dbt-training-wheels

Training wheels for dbt: takes the SQL you already write and turns it into dbt
models that match your project's existing conventions — explaining every step in
dbt's own terms, so the wheels can eventually come off.

**Status: ground-up rebuild in progress.** The design is being implemented slice
by slice; the previous proof-of-concept application is preserved at the
[`demo`](../../tree/demo) tag.

## Development

```
pip install -e ".[dev]"
pytest -q
```

MIT licensed.
