# orders_raw

Raw orders, copied from the source system every night by the extractor.

| Property | Value |
| --- | --- |
| Extract key | `orders` |
| Source table (dev) | `SALES_SRC_DEV.ORDERS` |
| Read by | [`stg_orders`](../../03_Silver/entities/stg_orders.md) |

## The SQL the extract runs

<!-- auto:start extract-sql extract=orders -->
```sql
SELECT
    order_id,
    customer_id,
    order_dt,
    city_nm,
    region_cd,
    order_status,
    gross_amt,
    discount_amt
FROM {source_table}
WHERE order_dt >= DATEADD(month, -36, CURRENT_DATE)
```
<!-- auto:end -->

The extract keeps the last three years of orders. Older orders stay in the source system only.
