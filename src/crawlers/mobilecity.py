"""MobileCity: the category lists one card per model without capacity, so prices come from
the variant list on each product page (one item per capacity, lowest price across colors)."""
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from src.crawlers.base import Fetcher, now_utc
from src.matching import in_scope, is_new_vna, parse_capacity, parse_model

STORE = "MobileCity"
BASE_URL = "https://mobilecity.vn"
CATEGORY_SLUG = "iphone-chinh-hang-vna"
CATEGORY_URL = f"{BASE_URL}/dien-thoai-{CATEGORY_SLUG}"
# The "Xem them" button posts to this endpoint, 20 products per page.
VIEW_MORE_URL = f"{BASE_URL}/product_view_more"
MAX_PAGES = 10
VARIANTS = re.compile(r"variants\s*=\s*(\[.*?\])\s*;", re.S)


def listing_links(html: str) -> list[tuple[str, str]]:
    """(title, url) of every product card in a category page or view-more fragment."""
    soup = BeautifulSoup(html, "lxml")
    return [(a.get_text(" ", strip=True), a["href"]) for a in soup.select("p.name a[href]")]


def wanted(title: str) -> bool:
    model = parse_model(title)
    return bool(model) and in_scope(model) and is_new_vna(title)


def parse_product(html: str, url: str, crawled_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    match = VARIANTS.search(html)
    if not match:
        return []
    storages = {
        int(item["data-storage_id"]): item.get_text(strip=True)
        for item in soup.select("div.storage-item[data-storage_id]")
    }
    name, in_stock = product_name_and_stock(soup)

    cheapest: dict[str, dict[str, Any]] = {}
    for variant in json.loads(match.group(1)):
        capacity = parse_capacity(storages.get(variant.get("storage"), "") or variant.get("variant_text", ""))
        price = variant.get("price")
        if not capacity or not price:
            continue
        if capacity not in cheapest or price < cheapest[capacity]["price"]:
            cheapest[capacity] = {
                "store": STORE,
                "product_id": f"{variant['phone_id']}-{capacity}",
                "product_name": f"{name} {capacity.upper()}",
                "price": int(price),
                "original_price": None,
                "capacity": capacity,
                "color": None,
                "in_stock": in_stock,
                "url": url,
                "crawled_at": crawled_at,
            }
    return list(cheapest.values())


def product_name_and_stock(soup: BeautifulSoup) -> tuple[str, bool | None]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for node in data.get("@graph", [data]) if isinstance(data, dict) else data:
            if isinstance(node, dict) and node.get("@type") == "Product":
                availability = str((node.get("offers") or {}).get("availability", ""))
                return node.get("name", "").strip(), ("InStock" in availability) if availability else None
    title = soup.select_one("h1")
    return (title.get_text(" ", strip=True) if title else ""), None


def crawl(fetcher: Fetcher) -> list[dict[str, Any]]:
    category = fetcher.get(CATEGORY_URL)
    links = listing_links(category)
    token = BeautifulSoup(category, "lxml").select_one('meta[name="csrf-token"]')
    for page in range(2, MAX_PAGES + 1):
        response = fetcher.post(
            VIEW_MORE_URL,
            data={"count": 20, "slug": CATEGORY_SLUG, "page": page, "type_category": "phone_categories"},
            headers={"X-CSRF-TOKEN": token["content"] if token else "", "X-Requested-With": "XMLHttpRequest"},
        )
        fragment = "".join(response.json().get("phone_item") or [])
        new = listing_links(fragment)
        if not new:
            break
        links += new

    urls = list(dict.fromkeys(url for title, url in links if wanted(title)))
    items = []
    for url in urls:
        items += parse_product(fetcher.get(url), url, now_utc())
    return items
