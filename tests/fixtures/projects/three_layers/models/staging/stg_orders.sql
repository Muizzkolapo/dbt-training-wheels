SELECT id, customer_id, amount FROM {{ source('raw', 'orders') }}
