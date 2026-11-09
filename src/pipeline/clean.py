"""Clean, normalize, deduplicate and match raw records, then upsert them into PostgreSQL.

Rejected records go to iphone_quarantine with the reason, unchanged.
"""
import json
import logging
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy import Engine, text

from src.matching import build_match_key, in_scope, is_new_vna, parse_capacity, parse_model
from src.pipeline.crawl import load_raw

logger = logging.getLogger(__name__)

MIN_PRICE = 1_000_000
MAX_PRICE = 120_000_000
# Allowed price range relative to the product's median price in the batch.
MARKET_BAND = (0.65, 1.6)
DATA_ORIGIN = "own_crawl"


def reject_reason(record: dict[str, Any]) -> str | None:
    price = record.get("price")
    if price is None:
        return "missing_price"
    if not MIN_PRICE <= price <= MAX_PRICE:
        return "price_out_of_range"
    name = record.get("product_name") or ""
    if not is_new_vna(name):
        return "not_new_vna"
    model = parse_model(name)
    if not model:
        return "unknown_model"
    if not in_scope(model):
        return "out_of_scope_model"
    if not (record.get("capacity") or parse_capacity(name)):
        return "unknown_capacity"
    return None


def clean_records(
    records: list[dict[str, Any]], data_origin: str = DATA_ORIGIN
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (clean rows, rejected records with a reason). Duplicates keep the lowest price."""
    clean: dict[tuple[str, str, str], dict[str, Any]] = {}
    rejected = []
    for record in records:
        if reason := reject_reason(record):
            rejected.append({"reason": reason, "record": record})
            continue
        name = record["product_name"]
        model = parse_model(name)
        capacity = record.get("capacity") or parse_capacity(name)
        original = record.get("original_price")
        row = {
            "store": record["store"],
            "product_id": str(record["product_id"]),
            "crawl_date": record["crawl_date"],
            "product_name": name,
            "model": model,
            "capacity": capacity,
            "color": record.get("color"),
            "price": record["price"],
            # A list price below the selling price is a crawl error (session 4), so drop it.
            "original_price": original if original and original >= record["price"] else None,
            "in_stock": record.get("in_stock"),
            "url": record.get("url"),
            "product_match_key": build_match_key(model, capacity),
            "data_origin": data_origin,
            "crawled_at": record.get("crawled_at"),
        }
        key = (row["store"], row["product_id"], row["crawl_date"])
        if key not in clean or row["price"] < clean[key]["price"]:
            clean[key] = row
    kept, outliers = drop_market_outliers(list(clean.values()))
    return kept, rejected + outliers


def drop_market_outliers(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject prices far from the market price of the same product in the batch.

    Some course records are wrong by a factor of 3 to 6 while staying under MAX_PRICE
    (an iPhone 15 128GB at 115 million), so a fixed range alone does not catch them.
    The market price is the median across stores of each store's median, so a store with
    many rows (Apple lists every color) does not outweigh the others.
    """
    prices: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        prices[row["product_match_key"]][row["store"]].append(row["price"])
    medians = {
        key: median(median(values) for values in by_store.values()) for key, by_store in prices.items()
    }
    low, high = MARKET_BAND
    kept, rejected = [], []
    for row in rows:
        if low <= row["price"] / medians[row["product_match_key"]] <= high:
            kept.append(row)
        else:
            rejected.append({"reason": "price_far_from_market", "record": row})
    return kept, rejected


UPSERT = text("""
    INSERT INTO iphone_clean_products (store, product_id, crawl_date, product_name, model, capacity, color,
        price, original_price, in_stock, url, product_match_key, data_origin, crawled_at)
    VALUES (:store, :product_id, :crawl_date, :product_name, :model, :capacity, :color,
        :price, :original_price, :in_stock, :url, :product_match_key, :data_origin, :crawled_at)
    ON CONFLICT (store, product_id, crawl_date) DO UPDATE SET
        product_name = EXCLUDED.product_name, model = EXCLUDED.model, capacity = EXCLUDED.capacity,
        color = EXCLUDED.color, price = EXCLUDED.price, original_price = EXCLUDED.original_price,
        in_stock = EXCLUDED.in_stock, url = EXCLUDED.url, product_match_key = EXCLUDED.product_match_key,
        data_origin = EXCLUDED.data_origin, crawled_at = EXCLUDED.crawled_at, loaded_at = now()
""")


def load(
    engine: Engine,
    records: list[dict[str, Any]],
    clean: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    data_origin: str = DATA_ORIGIN,
) -> None:
    """Replace the rows of every (store, crawl_date) present in `records` with the new result,
    so a re-run never leaves rows from an earlier run behind."""
    scope = {
        "origin": data_origin,
        "days": sorted({date.fromisoformat(r["crawl_date"]) for r in records}),
        "stores": sorted({r["store"] for r in records}),
    }
    where = "data_origin = :origin AND crawl_date = ANY(:days) AND store = ANY(:stores)"
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM iphone_clean_products WHERE {where}"), scope)
        if clean:
            conn.execute(UPSERT, clean)
        conn.execute(text(f"DELETE FROM iphone_quarantine WHERE {where}"), scope)
        if rejected:
            conn.execute(
                text("""
                    INSERT INTO iphone_quarantine (store, crawl_date, reason, raw_record, data_origin)
                    VALUES (:store, :crawl_date, :reason, CAST(:raw_record AS jsonb), :data_origin)
                """),
                [
                    {
                        "store": item["record"].get("store"),
                        "crawl_date": item["record"]["crawl_date"],
                        "reason": item["reason"],
                        "raw_record": json.dumps(item["record"], ensure_ascii=False, default=str),
                        "data_origin": data_origin,
                    }
                    for item in rejected
                ],
            )


def run(engine: Engine, crawl_date: date) -> None:
    raw = load_raw(crawl_date)
    clean, rejected = clean_records(raw)
    load(engine, raw, clean, rejected)
    logger.info("Loaded %d clean rows, quarantined %d of %d raw records", len(clean), len(rejected), len(raw))
