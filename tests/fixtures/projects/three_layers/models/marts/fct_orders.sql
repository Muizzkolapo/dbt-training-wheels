SELECT id, amount FROM {{ ref('int_orders_joined') }}
