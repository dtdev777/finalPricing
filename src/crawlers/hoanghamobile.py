"""Hoang Ha Mobile: the paged iPhone category. Each card shows one model at its default capacity."""
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.crawlers.base import Fetcher, now_utc, parse_price
from src.matching import parse_capacity

STORE = "Hoang Ha Mobile"
CATEGORY_URL = "https://hoanghamobile.com/dien-thoai-di-dong/iphone"
MAX_PAGES = 10


def parse_listing(html: str, crawled_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for card in soup.select("article[data-category-product]"):
        link = next((a for a in card.select("a[data-product-detail-link]") if a.get_text(strip=True)), None)
        price = card.select_one("[data-product-price]")
        if not link or not price:
            continue
        name = link.get_text(" ", strip=True)
        cta = card.select_one("[data-sku]")
        original = card.select_one("[data-product-last-price]")
        items.append({
            "store": STORE,
            "product_id": cta["data-sku"] if cta else card["data-product-id"],
            "product_name": name,
            "price": parse_price(price.get_text()),
            "original_price": parse_price(original.get_text()) if original else None,
            "capacity": parse_capacity(name),
            "color": None,
            "in_stock": None,
            "url": urljoin(CATEGORY_URL, link["href"]),
            "crawled_at": crawled_at,
        })
    return items


def crawl(fetcher: Fetcher) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        items = parse_listing(fetcher.get(f"{CATEGORY_URL}?p={page}"), now_utc())
        new = [item for item in items if item["product_id"] not in unique]
        if not new:
            break
        unique |= {item["product_id"]: item for item in new}
    return list(unique.values())
