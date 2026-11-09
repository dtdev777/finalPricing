"""Read-only REST API over the PostgreSQL analytics tables."""
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from config.settings import own_store
from src.api import assistant
from src.api.db import get_connection

app = FastAPI(title="finalPricing API", version="0.1.0")

Db = Annotated[Connection, Depends(get_connection)]
Limit = Annotated[int, Query(ge=1, le=1000)]


def fetch_all(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    rows = conn.execute(text(sql), params).mappings().all()
    return [
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in rows
    ]


@app.exception_handler(OperationalError)
def database_unavailable(_: Request, __: OperationalError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/health")
def health(conn: Db) -> dict[str, str]:
    conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/v1/kpis")
def kpis(conn: Db, store: str | None = None) -> dict[str, Any]:
    rows = fetch_all(
        conn,
        """
        WITH latest AS (SELECT max(crawl_date) AS crawl_date FROM iphone_clean_products)
        SELECT
            l.crawl_date AS latest_crawl_date,
            (SELECT count(DISTINCT p.product_match_key) FROM iphone_clean_products p
              WHERE p.crawl_date = l.crawl_date AND p.store = :store) AS products_tracked,
            (SELECT count(DISTINCT p.store) FROM iphone_clean_products p
              WHERE p.crawl_date = l.crawl_date) AS stores_tracked,
            (SELECT count(*) FROM price_alerts a
              WHERE a.crawl_date = l.crawl_date) AS alerts_latest_day,
            (SELECT round(avg(x.price_index), 2) FROM iphone_price_analytics x
              WHERE x.crawl_date = l.crawl_date AND x.store = :store) AS avg_price_index
        FROM latest l
        """,
        store=store or own_store(),
    )
    return rows[0]


@app.get("/api/v1/products")
def latest_products(
    conn: Db, store: str | None = None, model: str | None = None, limit: Limit = 200
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT * FROM (
            SELECT DISTINCT ON (store, product_id)
                store, product_id, crawl_date, product_name, model, capacity, color,
                price, original_price, in_stock, url, product_match_key, data_origin
            FROM iphone_clean_products
            -- Only the latest crawl day, so stores that are no longer crawled do not show stale prices.
            WHERE crawl_date = (SELECT max(crawl_date) FROM iphone_clean_products)
              AND (CAST(:store AS text) IS NULL OR store = :store)
              AND (CAST(:model AS text) IS NULL OR model = :model)
            ORDER BY store, product_id, crawl_date DESC
        ) latest
        ORDER BY product_match_key, price
        LIMIT :limit
        """,
        store=store,
        model=model,
        limit=limit,
    )


@app.get("/api/v1/products/{match_key}/history")
def price_history(
    conn: Db,
    match_key: str,
    store: str | None = None,
    days: Annotated[int, Query(ge=1, le=800)] = 90,
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT store, crawl_date, min(price) AS price
        FROM iphone_clean_products
        WHERE product_match_key = :match_key
          AND (CAST(:store AS text) IS NULL OR store = :store)
          AND crawl_date >= (SELECT max(crawl_date) FROM iphone_clean_products) - CAST(:days AS integer)
        GROUP BY store, crawl_date
        ORDER BY crawl_date, store
        """,
        match_key=match_key,
        store=store,
        days=days,
    )


@app.get("/api/v1/alerts")
def alerts(
    conn: Db,
    severity: Literal["high", "medium", "low"] | None = None,
    limit: Limit = 100,
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT alert_id, crawl_date, alert_type, severity, store, product_match_key,
               old_price, new_price, change_pct, reason, suspected_data_error, acknowledged
        FROM price_alerts
        WHERE CAST(:severity AS text) IS NULL OR severity = :severity
        ORDER BY crawl_date DESC,
                 CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 alert_id DESC
        LIMIT :limit
        """,
        severity=severity,
        limit=limit,
    )


@app.get("/api/v1/forecasts/{match_key}")
def forecasts(conn: Db, match_key: str, store: str | None = None) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT store, forecast_date, model_name, predicted_price, lower_bound, upper_bound
        FROM price_forecasts
        WHERE product_match_key = :match_key
          AND (CAST(:store AS text) IS NULL OR store = :store)
        ORDER BY forecast_date, store
        """,
        match_key=match_key,
        store=store,
    )


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/v1/assistant")
def ask_assistant(conn: Db, body: Question) -> dict[str, Any]:
    return assistant.ask(conn, body.question)


@app.get("/api/v1/pipeline/runs")
def pipeline_runs(conn: Db, limit: Limit = 20) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT run_id, started_at, finished_at, status, message
        FROM pipeline_runs
        ORDER BY run_id DESC
        LIMIT :limit
        """,
        limit=limit,
    )
