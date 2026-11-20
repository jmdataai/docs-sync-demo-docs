# d_customer

One row per customer, with the customer segment used by the Revenue by Segment chart.

| Property | Value |
| --- | --- |
| Model file | `models/silver/d_customer.sql` |
| Materialisation | `view` |

## What it reads

<!-- auto:start reads model=d_customer readby=yes -->
Bronze sources: `customers_raw`.

Silver models: none.

Read by: [`f_sales`](f_sales.md).
<!-- auto:end -->

## Notes

Customers with no segment are shown as `UNKNOWN`.
