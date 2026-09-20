SELECT customer_id, name, first_order_at FROM {{ source('raw', 'customers') }}
