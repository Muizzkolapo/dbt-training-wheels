-- Order rollup, migrated from the warehouse
CREATE TABLE order_totals AS
SELECT o.id, o.amount FROM raw.orders AS o;

CREATE TABLE daily_rollup AS
SELECT id, SUM(amount) AS total FROM order_totals GROUP BY id;
