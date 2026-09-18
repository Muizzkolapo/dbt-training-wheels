"""The SQL scripts the web tests converse over.

A module rather than the conftest, so that a test importing them imports the
same object pytest does. pytest loads `conftest.py` as a plugin under its own
name as well as on the import path, so constants taken from there exist twice;
harmless for strings and a trap the day anything stateful joins them.
"""

from __future__ import annotations

# One append question and nothing else to answer.
ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"

# The same shape against a different table, for the tests that edit a script
# in place and must not disturb the one every other test reads.
ONE_APPEND_ELSEWHERE = "INSERT INTO daily_totals SELECT order_id, amount FROM stg_orders;\n"

# Two answerable Decisions from the two different families: a tier-2 pass key,
# which embeds the source path, and an assemble key, which does not.
VARIABLE_AND_APPEND = (
    "DECLARE @cutoff DATE = '2024-01-01';\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders "
    "WHERE order_date >= @cutoff;\n"
)

# A MERGE names its key in its ON clause, so every option this question offers
# already spells that key and none of them carries a columns_prompt.
ONE_MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)

# Nothing to answer at all.
NO_QUESTIONS = "SELECT id, name INTO dim_people FROM raw_people;\n"

# One statement sqlglot cannot parse, ahead of one it can: spec section 7
# requires the failure to render in place while the rest of the page still
# renders.
UNPARSEABLE_AND_APPEND = (
    "THIS IS NOT SQL AT ALL(((;\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
)

# Two projections that carry the same output name. `Subject.candidates` is
# deliberately not deduplicated, because the ambiguity is the user's to see.
DUPLICATE_CANDIDATES = (
    "INSERT INTO revenue_events SELECT o.amount, p.amount "
    "FROM orders AS o JOIN pay AS p ON o.id = p.id;\n"
)

# A star projection: the columns are not knowable at convert time, so
# `Subject.candidates` is empty and a picker has nothing to offer.
STAR_PROJECTION = "INSERT INTO revenue_events SELECT * FROM stg_orders;\n"
