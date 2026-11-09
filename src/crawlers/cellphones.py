"""CellphoneS: the iPhone category page plus one page per series, each listing up to 20 cards."""
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.crawlers.base import Fetcher, now_utc, parse_price
from src.matching import parse_capacity

STORE = "CellphoneS"
CATEGORY_URL = "https://cellphones.com.vn/mobile/apple.html"
SERIES_LINK = re.compile(r"/mobile/apple/iphone-(1[5-9]|air|duo)\.html$")


def series_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = {urljoin(CATEGORY_URL, a["href"]) for a in soup.select("a[href]") if SERIES_LINK.search(a["href"])}
    return sorted(links)


def parse_listing(html: str, crawled_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for card in soup.select("div.product-info-container"):
        link = card.select_one("a.product__link")
        name = card.select_one(".product__name h3")
        if not link or not name:
            continue
        name = name.get_text(" ", strip=True)
        url = urljoin(CATEGORY_URL, link["href"]).split("?")[0]
        capacity = parse_capacity(name)
        slug = urlsplit(url).path.rsplit("/", 1)[-1].removesuffix(".html")
        price = card.select_one("p.product__price--show")
        original = card.select_one("p.product__price--through")
        items.append({
            "store": STORE,
            "product_id": f"{slug}-{capacity}" if capacity and capacity not in slug else slug,
            "product_name": name,
            "price": parse_price(price.get_text()) if price else None,
            "original_price": parse_price(original.get_text()) if original else None,
            "capacity": capacity,
            "color": None,
            "in_stock": None,
            "url": url,
            "crawled_at": crawled_at,
        })
    return items


def crawl(fetcher: Fetcher) -> list[dict[str, Any]]:
    category = fetcher.get(CATEGORY_URL)
    items = parse_listing(category, now_utc())
    for url in series_links(category):
        items += parse_listing(fetcher.get(url), now_utc())
    unique = {item["product_id"]: item for item in items}
    return list(unique.values())
