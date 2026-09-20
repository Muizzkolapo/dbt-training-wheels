SELECT o.id, o.amount FROM {{ ref('stg_orders') }} AS o
