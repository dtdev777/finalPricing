"""Crawl every store, keep the raw records as JSONL on disk and in MongoDB.

One store failing does not stop the others. A re-run on the same day replaces that day's
raw records for the stores that answered, so the stage can be repeated safely.
"""
import json
import logging
from datetime import date
from typing import Any

from pymongo import MongoClient
from sqlalchemy import Engine

from config.settings import RAW_DIR, mongo_db, mongo_uri
from src.crawlers import cellphones, hoanghamobile, mobilecity
from src.crawlers.base import Fetcher

logger = logging.getLogger(__name__)

# FPT Shop answers every request with a Cloudflare challenge, so it has no crawler.
SPIDERS = [cellphones, hoanghamobile, mobilecity]
COLLECTION = "raw_products"


def crawl_stores(crawl_date: date) -> list[dict[str, Any]]:
    fetcher = Fetcher()
    records = []
    for spider in SPIDERS:
        try:
            items = spider.crawl(fetcher)
        except Exception:
            logger.exception("Crawler for %s failed", spider.STORE)
            continue
        logger.info("%s: %d records", spider.STORE, len(items))
        records += [{**item, "crawl_date": crawl_date.isoformat()} for item in items]
    if not records:
        raise RuntimeError("No store returned any records")
    return records


def save_jsonl(records: list[dict[str, Any]], crawl_date: date) -> None:
    folder = RAW_DIR / crawl_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "products.jsonl").open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_mongo(records: list[dict[str, Any]], crawl_date: date) -> None:
    stores = sorted({record["store"] for record in records})
    with MongoClient(mongo_uri(), serverSelectionTimeoutMS=5000) as client:
        collection = client[mongo_db()][COLLECTION]
        collection.delete_many({"crawl_date": crawl_date.isoformat(), "store": {"$in": stores}})
        collection.insert_many([dict(record) for record in records])


def load_raw(crawl_date: date) -> list[dict[str, Any]]:
    with MongoClient(mongo_uri(), serverSelectionTimeoutMS=5000) as client:
        collection = client[mongo_db()][COLLECTION]
        return list(collection.find({"crawl_date": crawl_date.isoformat()}, {"_id": 0}))


def run(_: Engine, crawl_date: date) -> None:
    records = crawl_stores(crawl_date)
    # Disk first, so the raw data survives a database failure.
    save_jsonl(records, crawl_date)
    save_mongo(records, crawl_date)
