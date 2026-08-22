-- example.customer_view
-- old table was demo_schema.old_table_name`
CREATE OR REPLACE TABLE `project.dataset.customer_view` AS

-- interactions summary
WITH interaction_calc AS (
  SELECT DISTINCT customer_id,
         MAX(event_ts) AS latest_update,
         COUNT(DISTINCT CASE WHEN is_app = TRUE THEN interaction_id END) AS app_interactions
  FROM `project.dataset.interaction_detail` d
  LEFT JOIN `project.dataset.interaction_summary` s USING (interaction_id)
  WHERE source = "customer"
    AND event_type = "Customer.Updated"
  GROUP BY 1
),

-- transactions summary
transaction_calc AS (
  SELECT DISTINCT
         customer_id,
         COUNT(DISTINCT CASE WHEN txn_date >= CURRENT_DATE() - 365 THEN record_id END) AS transactions,
         MAX(txn_date) AS latest_txn
  FROM `project.dataset.retail_transactions`
  GROUP BY 1
),

-- merged aggregation
aggregation AS (
  SELECT DISTINCT
         customer_id,
         customer_id,
         global_id,
         segment,
         COUNT(DISTINCT customer_id) OVER (PARTITION BY customer_id) AS cuid,
         COUNT(DISTINCT global_id) OVER (PARTITION BY customer_id) AS guid,
         latest_update,
         app_interactions,
         transactions,
         latest_txn,
         CASE WHEN LOWER(status) = "live" THEN 1 ELSE 0 END AS live_flag,
         MAX(created_date) OVER (PARTITION BY customer_id, customer_id, global_id) AS latest_record,
         CASE WHEN source IN ("app", "web") THEN 1 ELSE 0 END AS app_flag
  FROM `project.dataset.customer_dim`
  LEFT JOIN interaction_calc USING (global_id)
  LEFT JOIN transaction_calc USING (customer_id)
  WHERE is_member = TRUE
    AND LOWER(segment) IN ("a", "b", "c")
    AND customer_id NOT IN (0, -1)
),

ranking AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY customer_id
           ORDER BY
             live_flag DESC,
             app_interactions DESC,
             latest_update DESC,
             transactions DESC,
             latest_txn DESC,
             app_flag DESC,
             latest_record DESC,
             customer_id
         ) AS record_rank
  FROM aggregation
)

SELECT *
FROM ranking
WHERE record_rank = 1
;

-- second table (simplified)
CREATE OR REPLACE TABLE `project.dataset.customer_items` AS

WITH new_bridging AS (
  SELECT c.customer_id,
         c.customer_id,
         c.global_id,
         p.*
  FROM `project.dataset.customer_view` c
  INNER JOIN `project.dataset.customer_item_bridge` nb USING (global_id)
  INNER JOIN `project.dataset.item_dim` p USING (global_item_id)
  WHERE c.global_id IS NOT NULL
)

SELECT *
FROM new_bridging

UNION ALL

SELECT c.customer_id,
       c.customer_id,
       c.global_id,
       p.*
FROM `project.dataset.customer_view` c
INNER JOIN `project.dataset.customer_item_bridge2` ob USING (customer_id)
INNER JOIN `project.dataset.item_dim` p USING (item_unique_id)
WHERE c.customer_id NOT IN (SELECT DISTINCT customer_id FROM new_bridging)
  AND LOWER(ob.bridge_type) = "item"
;