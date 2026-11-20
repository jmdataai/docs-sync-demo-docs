# stg_orders

Orders with cleaned types and upper-case city names. One row per order.

| Property | Value |
| --- | --- |
| Model file | `models/silver/stg_orders.sql` |
| Materialisation | `table` |

## What it reads

<!-- auto:start reads model=stg_orders readby=yes -->
Bronze sources: `orders_raw`.

Silver models: none.

Read by: [`f_sales`](f_sales.md).
<!-- auto:end -->

## Notes

`city_nm` is trimmed and upper-cased here, so every later step sees one spelling per city.
