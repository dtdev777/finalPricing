"""Read-only, parameterized queries used by the assistant.

The LLM router can only call these functions; it never writes SQL. Derived numbers
(differences, percentages, changes) are computed here so the answer can quote them.
"""
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from config.settings import own_store
from src.matching import build_match_key

MAX_ROWS = 50
ANY_MODEL = "iphone-[0-9a-z-]+"
ANY_CAPACITY = "[0-9]+(gb|tb)"


def key_pattern(model: str | None, capacity: str | None) -> str:
    """Regex for product_match_key; a missing model or capacity matches all."""
    return build_match_key(model or ANY_MODEL, capacity or ANY_CAPACITY)


def fetch(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    rows = conn.execute(text(sql), params).mappings().all()
    return [
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in rows
    ]


def latest_prices(
    conn: Connection,
    model: str | None = None,
    capacity: str | None = None,
    store: str | None = None,
    reference: str | None = None,
) -> list[dict[str, Any]]:
    """Latest lowest price per store and product, cheapest first, with the gap to the reference store."""
    reference = reference or own_store()
    rows = fetch(
        conn,
        """
        WITH latest AS (
            SELECT product_match_key, max(crawl_date) AS crawl_date
            FROM iphone_clean_products
            WHERE product_match_key ~ ('^' || :pattern || '$')
            GROUP BY product_match_key
        )
        SELECT p.product_match_key, p.store, p.crawl_date, min(p.price) AS price
        FROM iphone_clean_products p
        JOIN latest l USING (product_match_key, crawl_date)
        GROUP BY p.product_match_key, p.store, p.crawl_date
        ORDER BY p.product_match_key, price
        """,
        pattern=key_pattern(model, capacity),
    )
    by_product: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_product.setdefault(row["product_match_key"], []).append(row)

    result = []
    for stores in by_product.values():
        cheapest = stores[0]["price"]
        ref_price = next((s["price"] for s in stores if s["store"] == reference), None)
        for rank, row in enumerate(stores, start=1):
            row = {**row, "rank": rank, "stores_compared": len(stores), "cheapest_price": cheapest}
            row["above_cheapest"] = row["price"] - cheapest
            if ref_price is not None:
                row["reference_store"] = reference
                row["diff_vs_reference"] = row["price"] - ref_price
                row["diff_pct_vs_reference"] = round(100 * (row["price"] - ref_price) / ref_price, 2)
            if store is None or row["store"] == store:
                result.append(row)
    return result[:MAX_ROWS]


def price_history(
    conn: Connection,
    model: str | None = None,
    capacity: str | None = None,
    store: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Per store and product: first, last, lowest and highest price over the last `days` days."""
    return fetch(
        conn,
        """
        WITH daily AS (
            SELECT store, product_match_key, crawl_date, min(price) AS price
            FROM iphone_clean_products
            WHERE product_match_key ~ ('^' || :pattern || '$')
              AND (CAST(:store AS text) IS NULL OR store = :store)
              AND crawl_date >= (SELECT max(crawl_date) FROM iphone_clean_products) - CAST(:days AS integer)
            GROUP BY store, product_match_key, crawl_date
        ),
        ranked AS (
            SELECT *,
                   first_value(price) OVER w AS first_price,
                   last_value(price) OVER w AS last_price
            FROM daily
            WINDOW w AS (PARTITION BY store, product_match_key ORDER BY crawl_date
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )
        SELECT store, product_match_key,
               min(crawl_date) AS from_date, max(crawl_date) AS to_date, count(*) AS days_with_data,
               min(first_price) AS first_price, min(last_price) AS last_price,
               min(price) AS lowest_price, max(price) AS highest_price,
               min(last_price) - min(first_price) AS change,
               round(100.0 * (min(last_price) - min(first_price)) / min(first_price), 2) AS change_pct
        FROM ranked
        GROUP BY store, product_match_key
        ORDER BY product_match_key, last_price
        LIMIT :limit
        """,
        pattern=key_pattern(model, capacity),
        store=store,
        days=days,
        limit=MAX_ROWS,
    )


def price_changes(
    conn: Connection,
    model: str | None = None,
    capacity: str | None = None,
    store: str | None = None,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Products whose latest price differs from the price `days` days earlier, biggest change first."""
    return fetch(
        conn,
        """
        WITH daily AS (
            SELECT store, product_match_key, crawl_date, min(price) AS price
            FROM iphone_clean_products
            WHERE product_match_key ~ ('^' || :pattern || '$')
              AND (CAST(:store AS text) IS NULL OR store = :store)
            GROUP BY store, product_match_key, crawl_date
        ),
        bounds AS (SELECT max(crawl_date) AS latest FROM daily),
        now AS (SELECT d.* FROM daily d JOIN bounds b ON d.crawl_date = b.latest),
        before AS (
            SELECT DISTINCT ON (d.store, d.product_match_key) d.store, d.product_match_key, d.crawl_date, d.price
            FROM daily d CROSS JOIN bounds b
            WHERE d.crawl_date <= b.latest - CAST(:days AS integer)
            ORDER BY d.store, d.product_match_key, d.crawl_date DESC
        )
        SELECT n.store, n.product_match_key,
               b.crawl_date AS from_date, b.price AS from_price,
               n.crawl_date AS to_date, n.price AS to_price,
               n.price - b.price AS change,
               round(100.0 * (n.price - b.price) / b.price, 2) AS change_pct
        FROM now n JOIN before b USING (store, product_match_key)
        WHERE n.price <> b.price
        ORDER BY abs(n.price - b.price) DESC
        LIMIT :limit
        """,
        pattern=key_pattern(model, capacity),
        store=store,
        days=days,
        limit=MAX_ROWS,
    )


def forecasts(
    conn: Connection,
    model: str | None = None,
    capacity: str | None = None,
    store: str | None = None,
) -> list[dict[str, Any]]:
    return fetch(
        conn,
        """
        SELECT store, product_match_key, forecast_date, model_name, predicted_price, lower_bound, upper_bound
        FROM price_forecasts
        WHERE product_match_key ~ ('^' || :pattern || '$') AND store = :store
        ORDER BY product_match_key, forecast_date, model_name
        LIMIT :limit
        """,
        pattern=key_pattern(model, capacity),
        store=store or own_store(),
        limit=MAX_ROWS,
    )


def alerts(
    conn: Connection,
    model: str | None = None,
    capacity: str | None = None,
    store: str | None = None,
    severity: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    return fetch(
        conn,
        """
        SELECT crawl_date, severity, alert_type, store, product_match_key,
               old_price, new_price, change_pct, reason, suspected_data_error
        FROM price_alerts
        WHERE product_match_key ~ ('^' || :pattern || '$')
          AND (CAST(:store AS text) IS NULL OR store = :store)
          AND (CAST(:severity AS text) IS NULL OR severity = :severity)
        ORDER BY crawl_date DESC, CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
        LIMIT :limit
        """,
        pattern=key_pattern(model, capacity) if model or capacity else ".*",
        store=store,
        severity=severity,
        limit=min(limit, MAX_ROWS),
    )


def market_position(conn: Connection, store: str | None = None) -> list[dict[str, Any]]:
    """Latest price position and price index of each product at a store."""
    return fetch(
        conn,
        """
        SELECT store, product_match_key, crawl_date, price, market_min, market_median,
               price_diff, price_diff_pct, price_position, price_index
        FROM iphone_price_analytics
        WHERE store = :store
          AND crawl_date = (SELECT max(crawl_date) FROM iphone_price_analytics WHERE store = :store)
        ORDER BY price_diff_pct DESC
        LIMIT :limit
        """,
        store=store or own_store(),
        limit=MAX_ROWS,
    )


def catalog(conn: Connection) -> list[dict[str, Any]]:
    """Products in the data: number of stores and the date range covered."""
    return fetch(
        conn,
        """
        SELECT product_match_key, count(DISTINCT store) AS stores,
               min(crawl_date) AS first_date, max(crawl_date) AS last_date
        FROM iphone_clean_products
        GROUP BY product_match_key
        ORDER BY product_match_key
        LIMIT :limit
        """,
        limit=MAX_ROWS,
    )
