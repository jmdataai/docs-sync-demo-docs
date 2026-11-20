# F_SALES_SUMMARY

The Gold table behind both Sales Insights charts. It is loaded from the Silver model
[`f_sales`](../../03_Silver/entities/f_sales.md) by the procedure `SP_LOAD_F_SALES_SUMMARY`.

## Clustering key

<!-- auto:start cluster-by table=F_SALES_SUMMARY -->
`sale_dt`
<!-- auto:end -->

## Columns

Column and Type come from the Snowflake migrations. The last column is written by people.

<!-- auto:start gold-columns table=F_SALES_SUMMARY -->
| Column | Type | What it is used for |
| --- | --- | --- |
| `sale_dt` | `date` | The date filter on both charts |
| `city_nm` | `varchar(50)` | The city on the Top Cities chart |
| `net_revenue_amt` | `number(18,2)` | The revenue shown on both charts |
| `customer_segment` | `varchar(30)` | The bars on the Revenue by Segment chart |
| `order_cnt` | `number(18,0)` | The order count on the Revenue by Segment tooltip |
| `region_cd` | `varchar(10)` | Used only by the active-region filter |
<!-- auto:end -->

## Example query (Snowflake)

```sql
SELECT city_nm, SUM(net_revenue_amt) AS revenue
FROM F_SALES_SUMMARY
WHERE sale_dt BETWEEN '2026-01-01' AND '2026-03-31'
GROUP BY city_nm
ORDER BY revenue DESC
LIMIT 10;
```
