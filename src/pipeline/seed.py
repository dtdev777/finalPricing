"""Load the course's 8-store crawl (08/09 to 30/10/2026) as price history.

The JSON files come from the course's MongoDB export and stay out of git. Run once, or again
after the files change:

    docker compose --profile jobs run --rm pipeline python -m src.pipeline.seed

The raw documents go to MongoDB unchanged, then through the same cleaning rules as the daily
crawl with data_origin 'teacher_seed'. Market positions and alerts are rebuilt for every day
loaded, so the alerts for those days are a replay, not alerts raised at the time.
"""
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from sqlalchemy import Engine, create_engine

from config.settings import SEED_DIR, mongo_db, mongo_uri, postgres_url
from src.alerts import rules
from src.matching import parse_capacity
from src.pipeline import analytics
from src.pipeline.clean import clean_records, load

logger = logging.getLogger(__name__)

DATA_ORIGIN = "teacher_seed"
COLLECTION = "raw_teacher_seed"
VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")
STORES = {
    "apple_store": "Apple Store",
    "cellphones": "CellphoneS",
    "fpt_shop": "FPT Shop",
    "hoanghamobile": "Hoang Ha Mobile",
    "mobilecity": "MobileCity",
    "tgdd": "TGDD",
    "topzone": "TopZone",
    "viettelstore": "Viettel Store",
}
PLAIN_NUMBER = re.compile(r"\d+(\.\d+)?")


def parse_amount(value: Any) -> int | None:
    """'38690000.00' -> 38690000; '38.990.000d' or '77,190,000 VND' -> the digits; '' -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if PLAIN_NUMBER.fullmatch(text):
        return int(float(text))
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def parse_timestamp(value: Any) -> datetime | None:
    """Mongo exports hold both ISO strings and {"$date": ...} objects."""
    if isinstance(value, dict):
        value = value.get("$date")
        if isinstance(value, dict):
            value = int(value.get("$numberLong", 0)) / 1000
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, VIETNAM)
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def to_record(raw: dict[str, Any], source: str) -> dict[str, Any] | None:
    """Map one exported document to the record shape the crawlers produce."""
    crawled_at = parse_timestamp(raw.get("crawled_at"))
    if crawled_at is None:
        return None
    title = (raw.get("product_title") or "").strip()
    capacity = parse_capacity(raw.get("capacity") or "") or parse_capacity(title)
    url = raw.get("url") or ""
    color = raw.get("color")
    product_id = raw.get("part_number") or "|".join(filter(None, [url, capacity, color]))
    return {
        "store": STORES[source],
        "product_id": product_id,
        "product_name": title,
        "price": parse_amount(raw.get("price_raw")),
        "original_price": parse_amount(raw.get("original_price")),
        "capacity": capacity,
        "color": color,
        "in_stock": None,
        "url": url or None,
        "crawled_at": crawled_at.isoformat(),
        "crawl_date": crawled_at.astimezone(VIETNAM).date().isoformat(),
    }


def read_files(folder: Path) -> list[tuple[str, dict[str, Any]]]:
    documents = []
    for path in sorted(folder.glob("*.json")):
        source = path.stem
        if source not in STORES:
            logger.warning("Skipping %s: unknown store file", path.name)
            continue
        documents += [(source, doc) for doc in json.loads(path.read_text(encoding="utf-8"))]
    return documents


def save_raw(documents: list[tuple[str, dict[str, Any]]]) -> None:
    with MongoClient(mongo_uri(), serverSelectionTimeoutMS=5000) as client:
        collection = client[mongo_db()][COLLECTION]
        collection.delete_many({})
        collection.insert_many(
            [{**{k: v for k, v in doc.items() if k != "_id"}, "seed_source": source} for source, doc in documents]
        )


def run(engine: Engine, folder: Path = SEED_DIR) -> None:
    documents = read_files(folder)
    if not documents:
        raise RuntimeError(f"No seed files in {folder}")
    save_raw(documents)

    records = [record for source, doc in documents if (record := to_record(doc, source))]
    clean, rejected = clean_records(records, DATA_ORIGIN)
    load(engine, records, clean, rejected, DATA_ORIGIN)
    logger.info(
        "Seed: %d documents, %d clean rows, %d quarantined, %d without a timestamp",
        len(documents), len(clean), len(rejected), len(documents) - len(records),
    )

    days = sorted({date.fromisoformat(record["crawl_date"]) for record in records})
    for day in days:
        analytics.run(engine, day)
        rules.run(engine, day)
    logger.info("Rebuilt market positions and alerts for %d days (%s to %s)", len(days), days[0], days[-1])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    run(create_engine(postgres_url()))
