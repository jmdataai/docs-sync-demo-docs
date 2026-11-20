# Sales Insights: product summary

Sales Insights shows how much each city and customer segment sells over a chosen period.
The screen has two charts:

- [Top Cities](../06_Charts/top_cities.md): the ten cities with the most revenue
- [Revenue by Segment](../06_Charts/revenue_by_segment.md): revenue and orders per customer segment

## How the data flows

1. The extractor copies orders and customers from the source system into Bronze
   (`orders_raw`, `customers_raw`).
2. dbt cleans them in Silver (`stg_orders`, `d_customer`) and adds them up by day in `f_sales`.
3. Snowflake holds the result in the Gold table `F_SALES_SUMMARY`, loaded by `SP_LOAD_F_SALES_SUMMARY`.
4. The Tenant API runs one query per chart against `F_SALES_SUMMARY`. The Experience API
   shapes the result for the screen.

## Things to know

- Revenue (`net_revenue_amt`) is the gross order amount for the day.
- City names (`city_nm`) are stored in upper case, so "Dublin" and "DUBLIN" count as one city.
- Both charts only show regions that are switched on in `D_REGION`.
