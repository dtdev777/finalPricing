"""Clean, normalize, deduplicate and match raw records, then upsert them into PostgreSQL.

Rejected records go to iphone_quarantine with the reason, unchanged.
"""
import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from src.matching import build_match_key, in_scope, is_new_vna, parse_capacity, parse_model
from src.pipeline.crawl import load_raw

logger = logging.getLogger(__name__)

MIN_PRICE = 1_000_000
MAX_PRICE = 120_000_000
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


def clean_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (clean rows, rejected records with a reason). Duplicates keep the lowest price."""
    clean: dict[tuple[str, str], dict[str, Any]] = {}
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
            "data_origin": DATA_ORIGIN,
            "crawled_at": record.get("crawled_at"),
        }
        key = (row["store"], row["product_id"])
        if key not in clean or row["price"] < clean[key]["price"]:
            clean[key] = row
    return list(clean.values()), rejected


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


def load(engine: Engine, crawl_date: date, clean: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        if clean:
            # A product a store dropped since an earlier run today should not linger.
            conn.execute(
                text("""
                    DELETE FROM iphone_clean_products
                    WHERE crawl_date = :day AND data_origin = :origin AND store = ANY(:stores)
                """),
                {"day": crawl_date, "origin": DATA_ORIGIN, "stores": sorted({row["store"] for row in clean})},
            )
            conn.execute(UPSERT, clean)
        conn.execute(
            text("DELETE FROM iphone_quarantine WHERE crawl_date = :day AND data_origin = :origin"),
            {"day": crawl_date, "origin": DATA_ORIGIN},
        )
        if rejected:
            conn.execute(
                text("""
                    INSERT INTO iphone_quarantine (store, crawl_date, reason, raw_record, data_origin)
                    VALUES (:store, :crawl_date, :reason, CAST(:raw_record AS jsonb), :data_origin)
                """),
                [
                    {
                        "store": item["record"].get("store"),
                        "crawl_date": crawl_date,
                        "reason": item["reason"],
                        "raw_record": json.dumps(item["record"], ensure_ascii=False, default=str),
                        "data_origin": DATA_ORIGIN,
                    }
                    for item in rejected
                ],
            )


def run(engine: Engine, crawl_date: date) -> None:
    raw = load_raw(crawl_date)
    clean, rejected = clean_records(raw)
    load(engine, crawl_date, clean, rejected)
    logger.info("Loaded %d clean rows, quarantined %d of %d raw records", len(clean), len(rejected), len(raw))
