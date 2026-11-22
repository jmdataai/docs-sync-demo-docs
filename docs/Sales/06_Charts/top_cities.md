# Top Cities chart

Shows the ten cities with the most revenue in the period the user picks.

| Property | Value |
| --- | --- |
| Tenant API query | `SalesChartRepository.getTopCities` |
| Gold table | [`F_SALES_SUMMARY`](../04_Gold/entities/f_sales_summary.md) |
| Notebook | [`top_cities_gold_query.ipynb`](../notebooks/top_cities_gold_query.ipynb) |

## The SQL the chart runs

<!-- auto:start screen-sql query=SalesChartRepository.getTopCities -->
```sql
SELECT s.city_nm, SUM(s.net_revenue_amt) AS revenue
FROM F_SALES_SUMMARY s
WHERE s.sale_dt BETWEEN :startDate
AND :endDate
AND s.region_cd IN (SELECT r.region_cd
FROM D_REGION r
WHERE r.active_flg = 'Y')
AND s.region_cd <> 'TEST'
GROUP BY s.city_nm
ORDER BY revenue DESC
LIMIT 10
```
<!-- auto:end -->

## Filters, in plain words

- **Period:** the start and end date the user picks on the screen.
- **Active regions only:** a region appears only if it is switched on in `D_REGION`.
- **Top ten:** cities are sorted by revenue and only the first ten are shown.
