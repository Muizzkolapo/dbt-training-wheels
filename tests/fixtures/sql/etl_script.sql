-- Daily revenue ETL, migrated from the legacy warehouse
USE analytics;

DECLARE @start_date DATE = '2024-01-01';

TRUNCATE TABLE stg_daily_revenue;

INSERT INTO stg_daily_revenue
SELECT order_id, amount FROM raw_orders WHERE order_date >= @start_date;

SELECT id, name INTO dim_customers FROM raw_customers;

GRANT SELECT ON dim_customers TO reporting;
