-- Queries behind every number in docs/final_report.md.
-- Run against the project database:
--   docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < docs/report_queries.sql
-- REPORT_DATE is the latest crawl day used in the report.
\set report_date '''2026-11-13'''
\set reference '''CellphoneS'''

\echo '== Q1 Clean rows by origin and store'
SELECT data_origin, store, count(*) AS rows, count(DISTINCT crawl_date) AS days,
       count(DISTINCT product_match_key) AS products, min(crawl_date) AS first_day, max(crawl_date) AS last_day
FROM iphone_clean_products GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== Q2 Quarantined rows by origin and reason'
SELECT data_origin, reason, count(*) AS rows FROM iphone_quarantine GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== Q3 Quarantine share by store (course data)'
SELECT c.store, c.clean, coalesce(q.quarantined, 0) AS quarantined,
       round(100.0 * coalesce(q.quarantined, 0) / (c.clean + coalesce(q.quarantined, 0)), 1) AS quarantined_pct
FROM (SELECT store, count(*) AS clean FROM iphone_clean_products WHERE data_origin = 'teacher_seed' GROUP BY 1) c
LEFT JOIN (SELECT store, count(*) AS quarantined FROM iphone_quarantine WHERE data_origin = 'teacher_seed' GROUP BY 1) q USING (store)
ORDER BY quarantined_pct DESC;

\echo '== Q4 Match rate on the report date: products a store sells that at least one other store also sells'
WITH p AS (SELECT DISTINCT store, product_match_key FROM iphone_clean_products WHERE crawl_date = :report_date),
n AS (SELECT product_match_key, count(*) AS stores FROM p GROUP BY 1)
SELECT p.store, count(*) AS products, count(*) FILTER (WHERE n.stores > 1) AS matched,
       round(100.0 * count(*) FILTER (WHERE n.stores > 1) / count(*), 1) AS match_rate_pct,
       count(*) FILTER (WHERE n.stores = 3) AS sold_by_all_three
FROM p JOIN n USING (product_match_key) GROUP BY 1 ORDER BY 1;

\echo '== Q5 Reference store position on the report date'
SELECT price_position, count(*) AS products, round(avg(price_index), 2) AS avg_index
FROM iphone_price_analytics WHERE store = :reference AND crawl_date = :report_date GROUP BY 1 ORDER BY 2 DESC;

\echo '== Q6 Price index per store on the report date (products sold by 2+ stores)'
SELECT a.store, count(*) AS products, round(avg(a.price_index), 2) AS avg_index,
       count(*) FILTER (WHERE a.price_position = 'cheapest') AS cheapest,
       count(*) FILTER (WHERE a.price_position = 'most_expensive') AS most_expensive
FROM iphone_price_analytics a
WHERE a.crawl_date = :report_date AND a.price_position <> 'only_store'
GROUP BY 1 ORDER BY 3;

\echo '== Q7 Reference store: largest gaps to the market median on the report date'
SELECT product_match_key, price, market_median, market_min, price_diff_pct, price_position
FROM iphone_price_analytics WHERE store = :reference AND crawl_date = :report_date
ORDER BY price_diff_pct DESC LIMIT 8;

\echo '== Q8 Reference store: cheapest positions on the report date'
SELECT product_match_key, price, market_median, price_diff_pct
FROM iphone_price_analytics WHERE store = :reference AND crawl_date = :report_date AND price_position = 'cheapest'
ORDER BY price_diff_pct;

\echo '== Q9 Spread between cheapest and dearest store per product on the report date'
SELECT product_match_key, min(market_min) AS low, min(market_max) AS high,
       min(market_max) - min(market_min) AS spread, round(100.0 * (min(market_max) - min(market_min)) / min(market_min), 1) AS spread_pct
FROM iphone_price_analytics WHERE crawl_date = :report_date AND price_position <> 'only_store'
GROUP BY 1 ORDER BY spread_pct DESC LIMIT 8;

\echo '== Q10 Average spread on the report date'
SELECT count(*) AS products, round(avg(spread_pct), 1) AS avg_spread_pct, round(avg(spread)) AS avg_spread
FROM (SELECT product_match_key, 100.0 * (min(market_max) - min(market_min)) / min(market_min) AS spread_pct,
             min(market_max) - min(market_min) AS spread
      FROM iphone_price_analytics WHERE crawl_date = :report_date AND price_position <> 'only_store' GROUP BY 1) s;

\echo '== Q11 How often prices change: share of consecutive-day pairs with a different price, by store'
WITH d AS (SELECT store, product_match_key, crawl_date, min(price) AS price FROM iphone_clean_products GROUP BY 1, 2, 3),
c AS (SELECT *, lag(price) OVER w AS prev, lag(crawl_date) OVER w AS prev_day
      FROM d WINDOW w AS (PARTITION BY store, product_match_key ORDER BY crawl_date))
SELECT store, count(*) AS day_pairs, count(*) FILTER (WHERE price <> prev) AS changes,
       round(100.0 * count(*) FILTER (WHERE price <> prev) / count(*), 2) AS change_rate_pct,
       round(avg(abs(price::numeric / prev - 1) * 100) FILTER (WHERE price <> prev), 2) AS avg_change_pct
FROM c WHERE prev IS NOT NULL AND crawl_date - prev_day = 1 GROUP BY 1 ORDER BY 4 DESC;

\echo '== Q12 Listed discount (original price above selling price) by store, latest day of each store'
WITH latest AS (SELECT store, max(crawl_date) AS crawl_date FROM iphone_clean_products GROUP BY 1)
SELECT p.store, p.crawl_date, count(*) AS rows, count(*) FILTER (WHERE p.original_price > p.price) AS discounted,
       round(avg(100.0 * (p.original_price - p.price) / p.original_price) FILTER (WHERE p.original_price > p.price), 1) AS avg_discount_pct
FROM iphone_clean_products p JOIN latest l USING (store, crawl_date) GROUP BY 1, 2 ORDER BY 1;

\echo '== Q13 Reference store price changes over the course period and since crawling began'
WITH d AS (SELECT product_match_key, crawl_date, min(price) AS price FROM iphone_clean_products WHERE store = :reference GROUP BY 1, 2),
f AS (SELECT DISTINCT ON (product_match_key) product_match_key, crawl_date, price FROM d ORDER BY product_match_key, crawl_date),
l AS (SELECT DISTINCT ON (product_match_key) product_match_key, crawl_date, price FROM d ORDER BY product_match_key, crawl_date DESC)
SELECT count(*) AS products, count(*) FILTER (WHERE l.price < f.price) AS cheaper, count(*) FILTER (WHERE l.price > f.price) AS dearer,
       count(*) FILTER (WHERE l.price = f.price) AS unchanged
FROM f JOIN l USING (product_match_key) WHERE f.crawl_date < l.crawl_date;

\echo '== Q14 Alerts by type, severity and period'
SELECT CASE WHEN crawl_date < '2026-11-09' THEN 'replay (course data)' ELSE 'live crawl' END AS period,
       alert_type, severity, count(*) AS alerts, sum(suspected_data_error::int) AS suspected_data_error
FROM price_alerts GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== Q15 Pipeline runs'
SELECT status, count(*) AS runs, min(started_at)::date AS first_run, max(started_at)::date AS last_run,
       round(avg(extract(epoch FROM finished_at - started_at))) AS avg_seconds
FROM pipeline_runs GROUP BY 1;
