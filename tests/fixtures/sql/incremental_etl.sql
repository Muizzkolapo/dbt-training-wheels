-- Event and dimension loads, migrated from the warehouse
INSERT INTO events
SELECT event_id, occurred_at FROM raw.events WHERE occurred_at > '2024-01-01';

MERGE INTO dim_customers AS t
USING raw.customers AS s
ON t.customer_id = s.customer_id
WHEN MATCHED THEN UPDATE SET t.name = s.name;

TRUNCATE TABLE daily_totals;

INSERT INTO daily_totals (day, total)
SELECT occurred_at, COUNT(*) FROM raw.events GROUP BY occurred_at;
