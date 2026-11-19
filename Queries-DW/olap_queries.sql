-- ---- 0. Loaded date range and year ----------------------------------
SELECT MIN(date_value) AS first_loaded_date, MAX(date_value) AS last_loaded_date,
       MAX(year_num)   AS loaded_year_used,  COUNT(DISTINCT date_value) AS loaded_days
FROM dim_time;

-- ---------------------------------------------------------------------
-- Q1. Top 5 revenue-generating products on weekdays and weekends, monthly drill-down.
-- ---------------------------------------------------------------------
WITH yr AS (SELECT MAX(year_num) AS y FROM dim_time),
prod_year AS (
    SELECT t.day_type, f.product_key, SUM(f.revenue) AS year_revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    WHERE t.year_num = (SELECT y FROM yr)
    GROUP BY t.day_type, f.product_key
),
ranked AS (
    SELECT py.*, ROW_NUMBER() OVER (PARTITION BY day_type ORDER BY year_revenue DESC, product_key) AS rnk
    FROM prod_year py
)
SELECT r.day_type, r.rnk AS top_rank, p.product_id, p.product_name, t.month_num,
       SUM(f.revenue) AS monthly_revenue, r.year_revenue
FROM ranked r
JOIN fact_sales f ON f.product_key = r.product_key
JOIN dim_time t   ON t.time_key = f.time_key AND t.day_type = r.day_type
JOIN dim_product p ON p.product_key = r.product_key
WHERE r.rnk <= 5 AND t.year_num = (SELECT y FROM yr)
GROUP BY r.day_type, r.rnk, p.product_id, p.product_name, t.month_num, r.year_revenue
ORDER BY r.day_type, r.rnk, t.month_num;

-- ---------------------------------------------------------------------
-- Q2. Customer demographics by purchase amount with city category (all loaded dates).
-- ---------------------------------------------------------------------
SELECT c.gender, c.age_group, c.city_category,
       SUM(f.revenue) AS total_purchase_amount, COUNT(*) AS transaction_lines
FROM fact_sales f JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.gender, c.age_group, c.city_category
ORDER BY c.gender, c.age_group, c.city_category;

-- ---------------------------------------------------------------------
-- Q3. Product category sales by customer occupation (all loaded dates).
-- ---------------------------------------------------------------------
SELECT p.product_category, c.occupation, SUM(f.revenue) AS total_sales
FROM fact_sales f
JOIN dim_product p  ON p.product_key  = f.product_key
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY p.product_category, c.occupation
ORDER BY p.product_category, total_sales DESC;

-- ---------------------------------------------------------------------
-- Q4. Total purchases by gender and age group with quarterly trend, loaded year 2011 (Q4 = Oct - 9 Dec).
-- ---------------------------------------------------------------------
WITH q AS (
    SELECT c.gender, c.age_group, t.quarter_num, SUM(f.revenue) AS purchase_amount
    FROM fact_sales f
    JOIN dim_customer c ON c.customer_key = f.customer_key
    JOIN dim_time t     ON t.time_key = f.time_key
    WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
    GROUP BY c.gender, c.age_group, t.quarter_num
)
SELECT gender, age_group, quarter_num, purchase_amount,
       ROUND(100 * (purchase_amount - LAG(purchase_amount) OVER w)
             / NULLIF(LAG(purchase_amount) OVER w, 0), 2) AS qoq_change_pct
FROM q
WINDOW w AS (PARTITION BY gender, age_group ORDER BY quarter_num)
ORDER BY gender, age_group, quarter_num;

-- ---------------------------------------------------------------------
-- Q5. Top 5 occupations by sales within each product category (all loaded dates).
-- ---------------------------------------------------------------------
WITH s AS (
    SELECT p.product_category, c.occupation, SUM(f.revenue) AS total_sales
    FROM fact_sales f
    JOIN dim_product p  ON p.product_key  = f.product_key
    JOIN dim_customer c ON c.customer_key = f.customer_key
    GROUP BY p.product_category, c.occupation
), r AS (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY product_category ORDER BY total_sales DESC, occupation) AS rnk
    FROM s
)
SELECT product_category, rnk AS top_rank, occupation, total_sales
FROM r WHERE rnk <= 5
ORDER BY product_category, rnk;

-- ---------------------------------------------------------------------
-- Q6. City category performance by marital status, monthly, latest six months present in the DW.
-- ---------------------------------------------------------------------
SELECT t.year_num, t.month_num, c.city_category, c.marital_status,
       SUM(f.revenue) AS purchase_amount
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
JOIN dim_time t     ON t.time_key = f.time_key
WHERE t.year_num * 12 + t.month_num >
      (SELECT MAX(year_num * 12 + month_num) FROM dim_time) - 6
GROUP BY t.year_num, t.month_num, c.city_category, c.marital_status
ORDER BY t.year_num, t.month_num, c.city_category, c.marital_status;

-- ---------------------------------------------------------------------
-- Q7. Average purchase amount by years stayed in the current city and gender (all loaded dates).
-- ---------------------------------------------------------------------
SELECT c.stay_in_current_city_years, c.gender,
       ROUND(AVG(f.revenue), 2)                                  AS avg_line_amount,
       ROUND(SUM(f.revenue) / COUNT(DISTINCT f.transaction_id), 2) AS avg_cart_amount,
       COUNT(*) AS transaction_lines
FROM fact_sales f JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.stay_in_current_city_years, c.gender
ORDER BY c.stay_in_current_city_years, c.gender;

-- ---------------------------------------------------------------------
-- Q8. Top revenue-generating city categories within each product category (all loaded dates).
-- ---------------------------------------------------------------------
SELECT product_category, city_category, total_revenue,
       RANK() OVER (PARTITION BY product_category ORDER BY total_revenue DESC) AS city_rank
FROM (
    SELECT p.product_category, c.city_category, SUM(f.revenue) AS total_revenue
    FROM fact_sales f
    JOIN dim_product p  ON p.product_key  = f.product_key
    JOIN dim_customer c ON c.customer_key = f.customer_key
    GROUP BY p.product_category, c.city_category
) x
ORDER BY product_category, city_rank;

-- ---------------------------------------------------------------------
-- Q9. Month-over-month sales growth % by product category, loaded year 2011 (Jan - 9 Dec 2011).
-- ---------------------------------------------------------------------
WITH m AS (
    SELECT p.product_category, t.month_num, SUM(f.revenue) AS monthly_sales
    FROM fact_sales f
    JOIN dim_product p ON p.product_key = f.product_key
    JOIN dim_time t    ON t.time_key = f.time_key
    WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
    GROUP BY p.product_category, t.month_num
)
SELECT product_category, month_num, monthly_sales,
       LAG(monthly_sales) OVER w AS previous_month_sales,
       ROUND(100 * (monthly_sales - LAG(monthly_sales) OVER w) / NULLIF(LAG(monthly_sales) OVER w, 0), 2)
           AS mom_growth_pct
FROM m
WINDOW w AS (PARTITION BY product_category ORDER BY month_num)
ORDER BY product_category, month_num;

-- ---------------------------------------------------------------------
-- Q10. Weekend vs weekday sales by age group, loaded year 2011 (Jan - 9 Dec 2011).
-- ---------------------------------------------------------------------
SELECT c.age_group,
       SUM(CASE WHEN t.day_type = 'Weekday' THEN f.revenue ELSE 0 END) AS weekday_sales,
       SUM(CASE WHEN t.day_type = 'Weekend' THEN f.revenue ELSE 0 END) AS weekend_sales,
       SUM(f.revenue) AS total_sales,
       ROUND(100 * SUM(CASE WHEN t.day_type = 'Weekend' THEN f.revenue ELSE 0 END) / SUM(f.revenue), 2)
           AS weekend_share_pct
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
JOIN dim_time t     ON t.time_key = f.time_key
WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
GROUP BY c.age_group
ORDER BY c.age_group;

-- ---------------------------------------------------------------------
-- Q11. Top 5 products by revenue on weekdays and on weekends, grouped by month, loaded year 2011.
-- ---------------------------------------------------------------------
WITH pm AS (
    SELECT t.month_num, t.day_type, f.product_key, SUM(f.revenue) AS revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
    GROUP BY t.month_num, t.day_type, f.product_key
), r AS (
    SELECT pm.*, ROW_NUMBER() OVER (PARTITION BY month_num, day_type ORDER BY revenue DESC, product_key) AS rnk
    FROM pm
)
SELECT r.month_num, r.day_type, r.rnk AS top_rank, p.product_id, p.product_name, r.revenue
FROM r JOIN dim_product p ON p.product_key = r.product_key
WHERE r.rnk <= 5
ORDER BY r.month_num, r.day_type, r.rnk;

-- ---------------------------------------------------------------------
-- Q12. Quarterly store revenue growth %, loaded year 2011 (Q4 = Oct - 9 Dec 2011).
-- ---------------------------------------------------------------------
WITH q AS (
    SELECT s.store_id, s.store_name, t.quarter_num, SUM(f.revenue) AS quarterly_revenue
    FROM fact_sales f
    JOIN dim_store s ON s.store_key = f.store_key
    JOIN dim_time t  ON t.time_key  = f.time_key
    WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
    GROUP BY s.store_id, s.store_name, t.quarter_num
)
SELECT store_id, store_name, quarter_num, quarterly_revenue,
       LAG(quarterly_revenue) OVER w AS previous_quarter_revenue,
       ROUND(100 * (quarterly_revenue - LAG(quarterly_revenue) OVER w)
             / NULLIF(LAG(quarterly_revenue) OVER w, 0), 2) AS qoq_growth_pct
FROM q
WINDOW w AS (PARTITION BY store_id ORDER BY quarter_num)
ORDER BY store_id, quarter_num;

-- ---------------------------------------------------------------------
-- Q13. Supplier sales contribution by store, then supplier, then product (all loaded dates).
-- ---------------------------------------------------------------------
SELECT s.store_name, su.supplier_name, p.product_id, p.product_name,
       SUM(f.revenue) AS product_revenue,
       ROUND(100 * SUM(f.revenue) / SUM(SUM(f.revenue)) OVER (PARTITION BY s.store_id), 4)
           AS pct_of_store,
       ROUND(100 * SUM(f.revenue) / SUM(SUM(f.revenue)) OVER (PARTITION BY s.store_id, su.supplier_id), 4)
           AS pct_of_supplier_in_store,
       ROUND(100 * SUM(SUM(f.revenue)) OVER (PARTITION BY s.store_id, su.supplier_id)
             / SUM(SUM(f.revenue)) OVER (PARTITION BY s.store_id), 2) AS supplier_share_of_store_pct
FROM fact_sales f
JOIN dim_store s     ON s.store_key    = f.store_key
JOIN dim_supplier su ON su.supplier_key = f.supplier_key
JOIN dim_product p   ON p.product_key  = f.product_key
GROUP BY s.store_id, s.store_name, su.supplier_id, su.supplier_name, p.product_id, p.product_name
ORDER BY s.store_name, su.supplier_name, product_revenue DESC;

-- ---------------------------------------------------------------------
-- Q14. Seasonal analysis of product sales (all loaded dates; Dec 2010 + Jan-Feb 2011 and Dec 2011 are Winter).
-- ---------------------------------------------------------------------
SELECT p.product_id, p.product_name,
       SUM(CASE WHEN t.season = 'Spring' THEN f.revenue ELSE 0 END) AS spring_sales,
       SUM(CASE WHEN t.season = 'Summer' THEN f.revenue ELSE 0 END) AS summer_sales,
       SUM(CASE WHEN t.season = 'Fall'   THEN f.revenue ELSE 0 END) AS fall_sales,
       SUM(CASE WHEN t.season = 'Winter' THEN f.revenue ELSE 0 END) AS winter_sales,
       SUM(f.revenue) AS total_sales
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
JOIN dim_time t    ON t.time_key = f.time_key
GROUP BY p.product_id, p.product_name
ORDER BY total_sales DESC;

-- ---------------------------------------------------------------------
-- Q15. Store- and supplier-wise month-to-month revenue volatility % (all loaded months, Dec 2010 - Dec 2011).
-- ---------------------------------------------------------------------
WITH m AS (
    SELECT s.store_id, s.store_name, su.supplier_id, su.supplier_name, t.year_num, t.month_num,
           SUM(f.revenue) AS monthly_revenue
    FROM fact_sales f
    JOIN dim_store s     ON s.store_key     = f.store_key
    JOIN dim_supplier su ON su.supplier_key = f.supplier_key
    JOIN dim_time t      ON t.time_key      = f.time_key
    GROUP BY s.store_id, s.store_name, su.supplier_id, su.supplier_name, t.year_num, t.month_num
)
SELECT store_name, supplier_name, year_num, month_num, monthly_revenue,
       LAG(monthly_revenue) OVER w AS previous_month_revenue,
       ROUND(100 * (monthly_revenue - LAG(monthly_revenue) OVER w)
             / NULLIF(LAG(monthly_revenue) OVER w, 0), 2) AS volatility_pct
FROM m
WINDOW w AS (PARTITION BY store_id, supplier_id ORDER BY year_num, month_num)
ORDER BY store_name, supplier_name, year_num, month_num;

-- ---------------------------------------------------------------------
-- Q16. Top 5 product pairs bought together (all loaded dates).
-- ---------------------------------------------------------------------
WITH cart_items AS (
    SELECT DISTINCT transaction_id, product_key FROM fact_sales
)
SELECT p1.product_id AS product_id_1, p1.product_name AS product_name_1,
       p2.product_id AS product_id_2, p2.product_name AS product_name_2,
       COUNT(*) AS carts_together
FROM cart_items a
JOIN cart_items b ON b.transaction_id = a.transaction_id AND b.product_key > a.product_key
JOIN dim_product p1 ON p1.product_key = a.product_key
JOIN dim_product p2 ON p2.product_key = b.product_key
GROUP BY a.product_key, b.product_key, p1.product_id, p1.product_name, p2.product_id, p2.product_name
ORDER BY carts_together DESC, p1.product_id, p2.product_id
LIMIT 5;

-- ---------------------------------------------------------------------
-- Q17. Yearly revenue by store, supplier and product with ROLLUP (2010 = Dec only, 2011 = Jan - 9 Dec).
-- ---------------------------------------------------------------------
SELECT t.year_num, s.store_name, su.supplier_name, p.product_id,
       IF(GROUPING(p.product_id) = 1, NULL, MAX(p.product_name)) AS product_name,
       SUM(f.revenue) AS revenue,
       CASE WHEN GROUPING(t.year_num) = 1 THEN 'GRAND TOTAL'
            WHEN GROUPING(s.store_name) = 1 THEN 'YEAR SUBTOTAL'
            WHEN GROUPING(su.supplier_name) = 1 THEN 'STORE SUBTOTAL'
            WHEN GROUPING(p.product_id) = 1 THEN 'SUPPLIER SUBTOTAL'
            ELSE 'PRODUCT' END AS row_type
FROM fact_sales f
JOIN dim_time t      ON t.time_key      = f.time_key
JOIN dim_store s     ON s.store_key     = f.store_key
JOIN dim_supplier su ON su.supplier_key = f.supplier_key
JOIN dim_product p   ON p.product_key   = f.product_key
GROUP BY t.year_num, s.store_name, su.supplier_name, p.product_id WITH ROLLUP
ORDER BY GROUPING(t.year_num), t.year_num, GROUPING(s.store_name), s.store_name,
         GROUPING(su.supplier_name), su.supplier_name, GROUPING(p.product_id), p.product_id;

-- ---------------------------------------------------------------------
-- Q18. Quantity and revenue per product for the first half, the second half and the full loaded year 2011.
-- ---------------------------------------------------------------------
SELECT p.product_id, p.product_name,
       SUM(CASE WHEN t.half_year = 'H1' THEN f.quantity ELSE 0 END) AS h1_quantity,
       SUM(CASE WHEN t.half_year = 'H1' THEN f.revenue  ELSE 0 END) AS h1_revenue,
       SUM(CASE WHEN t.half_year = 'H2' THEN f.quantity ELSE 0 END) AS h2_quantity,
       SUM(CASE WHEN t.half_year = 'H2' THEN f.revenue  ELSE 0 END) AS h2_revenue,
       SUM(f.quantity) AS year_quantity, SUM(f.revenue) AS year_revenue
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
JOIN dim_time t    ON t.time_key = f.time_key
WHERE t.year_num = (SELECT MAX(year_num) FROM dim_time)
GROUP BY p.product_id, p.product_name
ORDER BY year_revenue DESC;

-- ---------------------------------------------------------------------
-- Q19. High revenue spikes (all loaded days).
-- ---------------------------------------------------------------------
WITH daily AS (
    SELECT f.product_key, t.date_value, SUM(f.revenue) AS daily_revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    GROUP BY f.product_key, t.date_value
), d AS (
    SELECT daily.*, AVG(daily_revenue) OVER (PARTITION BY product_key) AS avg_daily_revenue
    FROM daily
)
SELECT p.product_id, p.product_name, d.date_value, d.daily_revenue,
       ROUND(d.avg_daily_revenue, 2) AS avg_daily_revenue,
       ROUND(d.daily_revenue / d.avg_daily_revenue, 2) AS times_average
FROM d JOIN dim_product p ON p.product_key = d.product_key
WHERE d.daily_revenue > 2 * d.avg_daily_revenue
ORDER BY times_average DESC, p.product_id, d.date_value;

-- Helper for the report comment on Q19 (counts and the busiest spike days):
WITH daily AS (
    SELECT f.product_key, t.date_value, SUM(f.revenue) AS daily_revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    GROUP BY f.product_key, t.date_value
), d AS (
    SELECT daily.*, AVG(daily_revenue) OVER (PARTITION BY product_key) AS avg_daily_revenue FROM daily
)
SELECT date_value, COUNT(*) AS products_spiking, ROUND(SUM(daily_revenue), 2) AS spike_revenue
FROM d WHERE daily_revenue > 2 * avg_daily_revenue
GROUP BY date_value ORDER BY products_spiking DESC LIMIT 10;

-- ---------------------------------------------------------------------
-- Q20. View STORE_QUARTERLY_SALES: total quarterly sales by store (all loaded dates; quarters of 2010 and 2011).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW STORE_QUARTERLY_SALES AS
SELECT s.store_id, s.store_name, t.year_num, t.quarter_num,
       SUM(f.revenue)  AS total_sales,
       SUM(f.quantity) AS total_quantity,
       COUNT(DISTINCT f.transaction_id) AS shopping_carts
FROM fact_sales f
JOIN dim_store s ON s.store_key = f.store_key
JOIN dim_time t  ON t.time_key  = f.time_key
GROUP BY s.store_id, s.store_name, t.year_num, t.quarter_num;

SELECT * FROM STORE_QUARTERLY_SALES ORDER BY store_name, year_num, quarter_num;

-- Materialized-view emulation (MySQL has no native materialized views): a stored copy of the view,
-- rebuilt on demand.  Drop and re-create it after every new load.
DROP TABLE IF EXISTS mv_store_quarterly_sales;
CREATE TABLE mv_store_quarterly_sales AS SELECT * FROM STORE_QUARTERLY_SALES;
SELECT * FROM mv_store_quarterly_sales ORDER BY store_name, year_num, quarter_num;
