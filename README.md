# dbt-training-wheels

Training wheels for dbt: takes the SQL you already write and turns it into dbt
models that match your project's existing conventions — explaining every step in
dbt's own terms, so the wheels can eventually come off.

**Status: ground-up rebuild in progress.** The design is being implemented slice
by slice; the previous proof-of-concept application is preserved at the
[`demo`](../../tree/demo) tag.

## What it remembers

Each conversion explains the dbt words it uses, where you meet them. After the
third time it has explained a word for a given project it stops repeating it:
the word is still named, and its meaning moves to the end of the report (and to
a quieter list in the browser), so a tenth conversion is about what is new
rather than about what you already read three times.

That is the only thing this tool keeps between runs. It is one plain JSON file
at `$XDG_STATE_HOME/dbtw/learned.json` (or `~/.local/state/dbtw/learned.json`),
holding a project path, a count of conversions, and how often each dbt word was
explained — **no SQL, and nothing about your data**. A `learned.json.lock`
beside it is held only while the file is being updated, so two runs at once
cannot lose each other's record. Delete the record to start the count again, or
pass `--no-remember` to `dbtw convert` / `dbtw web` to explain everything in
full every time and write nothing.

If that file cannot be read — damaged, unreadable, or written by a newer
version of this tool — the conversion still runs and still writes everything;
every word is explained in full, and the report says why. It is never
overwritten with what this run happened to understand.

## Development

```
pip install -e ".[dev]"
pytest -q
```

MIT licensed.
