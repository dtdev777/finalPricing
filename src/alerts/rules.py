"""Price alerts: a store changed a price, or a competitor moved below the reference store.

Severity follows the course rules: high above 20%, medium from 10% or for a lost price lead,
low otherwise. A jump this large is more often a crawl error than a real price, so it is
flagged as a suspected data error instead of reported as a competitor move.
"""
import logging
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from config.settings import own_store

logger = logging.getLogger(__name__)

MIN_CHANGE_PCT = 2.0
MEDIUM_CHANGE_PCT = 10.0
HIGH_CHANGE_PCT = 20.0
DATA_ERROR_PCT = 40.0

Prices = dict[tuple[str, str], int]


def severity(change_pct: float) -> str:
    size = abs(change_pct)
    return "high" if size > HIGH_CHANGE_PCT else "medium" if size >= MEDIUM_CHANGE_PCT else "low"


def price_change_alerts(today: Prices, previous: Prices, crawl_date: date) -> list[dict[str, Any]]:
    """today / previous: (store, product_match_key) -> lowest price on the day and on the last earlier day."""
    alerts = []
    for (store, key), new in today.items():
        old = previous.get((store, key))
        if not old or new == old:
            continue
        change_pct = (new / old - 1) * 100
        if abs(change_pct) < MIN_CHANGE_PCT:
            continue
        direction = "dropped" if new < old else "raised"
        alerts.append({
            "crawl_date": crawl_date,
            "alert_type": "price_drop" if new < old else "price_increase",
            "severity": severity(change_pct),
            "store": store,
            "product_match_key": key,
            "old_price": old,
            "new_price": new,
            "change_pct": round(change_pct, 3),
            "reason": f"{store} {direction} the price by {abs(change_pct):.1f}% since the last crawl.",
            "suspected_data_error": abs(change_pct) >= DATA_ERROR_PCT,
        })
    return alerts


def undercut_alerts(today: Prices, previous: Prices, crawl_date: date, reference: str) -> list[dict[str, Any]]:
    """A competitor is now cheaper than the reference store, and was not on the last earlier day."""
    alerts = []
    for (store, key), new in today.items():
        own_now = today.get((reference, key))
        if store == reference or own_now is None or new >= own_now:
            continue
        old, own_before = previous.get((store, key)), previous.get((reference, key))
        if old is not None and own_before is not None and old < own_before:
            continue
        gap_pct = (new / own_now - 1) * 100
        alerts.append({
            "crawl_date": crawl_date,
            "alert_type": "undercut",
            "severity": "high" if abs(gap_pct) > HIGH_CHANGE_PCT else "medium",
            "store": store,
            "product_match_key": key,
            "old_price": own_now,
            "new_price": new,
            "change_pct": round(gap_pct, 3),
            "reason": f"{store} is now {abs(gap_pct):.1f}% cheaper than {reference}.",
            "suspected_data_error": abs(gap_pct) >= DATA_ERROR_PCT,
        })
    return alerts


def daily_prices(conn, sql: str, crawl_date: date) -> Prices:
    rows = conn.execute(text(sql), {"day": crawl_date}).all()
    return {(store, key): int(price) for store, key, price in rows}


def run(engine: Engine, crawl_date: date) -> None:
    with engine.begin() as conn:
        today = daily_prices(
            conn,
            """
            SELECT store, product_match_key, min(price) FROM iphone_clean_products
            WHERE crawl_date = :day AND product_match_key IS NOT NULL
            GROUP BY store, product_match_key
            """,
            crawl_date,
        )
        previous = daily_prices(
            conn,
            """
            SELECT DISTINCT ON (store, product_match_key) store, product_match_key, price
            FROM (
                SELECT store, product_match_key, crawl_date, min(price) AS price
                FROM iphone_clean_products
                WHERE crawl_date < :day AND product_match_key IS NOT NULL
                GROUP BY store, product_match_key, crawl_date
            ) daily
            ORDER BY store, product_match_key, crawl_date DESC
            """,
            crawl_date,
        )
        alerts = price_change_alerts(today, previous, crawl_date)
        alerts += undercut_alerts(today, previous, crawl_date, own_store())
        if alerts:
            # Upsert, so a re-run keeps the acknowledged flag of alerts already raised today.
            conn.execute(
                text("""
                    INSERT INTO price_alerts (crawl_date, alert_type, severity, store, product_match_key,
                        old_price, new_price, change_pct, reason, suspected_data_error)
                    VALUES (:crawl_date, :alert_type, :severity, :store, :product_match_key,
                        :old_price, :new_price, :change_pct, :reason, :suspected_data_error)
                    ON CONFLICT (alert_type, store, product_match_key, crawl_date) DO UPDATE SET
                        severity = EXCLUDED.severity, old_price = EXCLUDED.old_price,
                        new_price = EXCLUDED.new_price, change_pct = EXCLUDED.change_pct,
                        reason = EXCLUDED.reason, suspected_data_error = EXCLUDED.suspected_data_error
                """),
                alerts,
            )
    logger.info("Raised %d alerts", len(alerts))
