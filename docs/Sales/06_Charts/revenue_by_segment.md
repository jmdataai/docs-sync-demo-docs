# Revenue by Segment chart

Shows revenue and order count for each customer segment in the period the user picks.

| Property | Value |
| --- | --- |
| Tenant API query | `SalesChartRepository.getRevenueBySegment` |
| Gold table | [`F_SALES_SUMMARY`](../04_Gold/entities/f_sales_summary.md) |

## The SQL the chart runs

<!-- auto:start screen-sql query=SalesChartRepository.getRevenueBySegment -->
```sql
SELECT s.customer_segment, SUM(s.net_revenue_amt) AS revenue, SUM(s.order_cnt) AS orders
FROM F_SALES_SUMMARY s
WHERE s.sale_dt BETWEEN :startDate
AND :endDate
AND s.region_cd IN (SELECT r.region_cd
FROM D_REGION r
WHERE r.active_flg = 'Y')
AND s.region_cd <> 'TEST'
GROUP BY s.customer_segment
ORDER BY revenue DESC
```
<!-- auto:end -->

## Filters, in plain words

- **Period:** the start and end date the user picks on the screen.
- **Active regions only:** a region appears only if it is switched on in `D_REGION`.
