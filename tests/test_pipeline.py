from datetime import date

import pandas as pd
import pytest

from src.alerts import rules
from src.crawlers import cellphones, hoanghamobile, mobilecity
from src.crawlers.base import parse_price
from src.matching import in_scope, is_new_vna, parse_model
from src.pipeline.analytics import market_rows, price_position
from src.pipeline.clean import clean_records

NOW = "2026-11-09T00:00:00+00:00"
DAY = date(2026, 11, 9)


@pytest.mark.parametrize(
    ("text", "price"), [("38.690.000\u0111", 38_690_000), ("24,990,000 VND", 24_990_000), ("Lien he", None), (None, None)]
)
def test_parse_price(text, price):
    assert parse_price(text) == price


def test_scope_and_condition_filters():
    assert parse_model("iPhone Duo 256GB") == "iphone-duo"
    assert in_scope("iphone-17e") and in_scope("iphone-air") and not in_scope("iphone-14-pro")
    assert is_new_vna("iPhone 17 Pro Max 256GB | Ch\u00ednh h\u00e3ng VN/A")
    assert not is_new_vna("iPhone 15 CPO Qu\u1ed1c t\u1ebf")
    assert not is_new_vna("iPhone 16 Pro c\u0169 99%")


def test_cellphones_listing():
    html = """
    <div class="product-info-container">
      <a class="product__link" href="/iphone-17-pro-max.html?x=1"></a>
      <div class="product__name"><h3>iPhone 17 Pro Max 512GB | Chinh hang</h3></div>
      <p class="product__price--show">41.390.000d</p><p class="product__price--through">42.990.000d</p>
    </div>
    <a href="https://cellphones.com.vn/mobile/apple/iphone-17.html">17</a>
    <a href="https://cellphones.com.vn/mobile/apple/iphone-13.html">13</a>"""
    [item] = cellphones.parse_listing(html, NOW)
    assert item["product_id"] == "iphone-17-pro-max-512gb"
    assert (item["price"], item["original_price"]) == (41_390_000, 42_990_000)
    assert item["url"] == "https://cellphones.com.vn/iphone-17-pro-max.html"
    assert cellphones.series_links(html) == ["https://cellphones.com.vn/mobile/apple/iphone-17.html"]


def test_hoanghamobile_listing():
    html = """
    <article data-category-product data-product-id="8905">
      <a data-product-detail-link href="/dien-thoai/iphone-18-pro-max"><img alt=""></a>
      <a data-product-detail-link href="/dien-thoai/iphone-18-pro-max">iPhone 18 Pro Max 256GB</a>
      <p data-product-price>41.490.000d</p><p data-product-last-price></p>
      <button data-sku="MJXN4X"></button>
    </article>"""
    [item] = hoanghamobile.parse_listing(html, NOW)
    assert (item["product_id"], item["capacity"], item["price"]) == ("MJXN4X", "256gb", 41_490_000)
    assert item["original_price"] is None


def test_mobilecity_product_keeps_cheapest_color_per_capacity():
    html = """
    <script type="application/ld+json">{"@graph": [{"@type": "Product", "name": "iPhone 17 Chinh hang",
      "offers": {"availability": "https://schema.org/InStock"}}]}</script>
    <div class="storage-item" data-storage_id="253">256GB</div>
    <div class="storage-item" data-storage_id="254">512GB</div>
    <script>var variants = [
      {"id": 1, "phone_id": 2740, "storage": 253, "price": 24990000, "variant_text": "256GB Blue"},
      {"id": 2, "phone_id": 2740, "storage": 253, "price": 24790000, "variant_text": "256GB Black"},
      {"id": 3, "phone_id": 2740, "storage": 254, "price": 31490000, "variant_text": "512GB Blue"}];</script>"""
    items = mobilecity.parse_product(html, "https://mobilecity.vn/x.html", NOW)
    assert [(i["product_id"], i["price"]) for i in items] == [("2740-256gb", 24_790_000), ("2740-512gb", 31_490_000)]
    assert items[0]["product_name"] == "iPhone 17 Chinh hang 256GB" and items[0]["in_stock"] is True
    assert mobilecity.wanted("iPhone 16e Chinh hang VN/A") and not mobilecity.wanted("iPhone 12 CPO Quoc te")


def raw(name, price, product_id="p1", store="CellphoneS", **extra):
    return {"store": store, "product_id": product_id, "product_name": name, "price": price,
            "crawl_date": "2026-11-09", **extra}


def test_clean_records_rejects_with_reason_and_keeps_lowest_duplicate():
    clean, rejected = clean_records([
        raw("iPhone 17 256GB", 24_990_000, original_price=20_000_000),
        raw("iPhone 17 256GB", 24_790_000),
        raw("iPhone 17 256GB", 905_540_000, "p2"),
        raw("iPhone 17 256GB", None, "p3"),
        raw("iPhone 14 128GB", 14_790_000, "p4"),
        raw("iPhone 16 Pro CPO", 20_000_000, "p5"),
        raw("iPhone 17 Pro Max", 33_000_000, "p6"),
    ])
    assert [(r["product_match_key"], r["price"], r["original_price"]) for r in clean] == [
        ("iphone-17-256gb", 24_790_000, None)
    ]
    assert [r["reason"] for r in rejected] == [
        "price_out_of_range", "missing_price", "out_of_scope_model", "not_new_vna", "unknown_capacity"
    ]


@pytest.mark.parametrize(
    ("price", "position"),
    [(100, "cheapest"), (104, "below_market"), (110, "at_market"), (115, "above_market"), (130, "most_expensive")],
)
def test_price_position(price, position):
    assert price_position(price, low=100, high=130, median=110, stores=5) == position


def test_market_rows():
    prices = pd.DataFrame(
        [("A", "k", DAY, 100), ("B", "k", DAY, 120), ("C", "k", DAY, 110), ("A", "solo", DAY, 50)],
        columns=["store", "product_match_key", "crawl_date", "price"],
    )
    rows = market_rows(prices).set_index(["store", "product_match_key"])
    assert rows.loc[("C", "k"), "price_index"] == 100.0
    assert rows.loc[("B", "k"), "price_diff"] == 20
    assert rows.loc[("A", "solo"), "price_position"] == "only_store"


def test_price_change_alerts():
    previous = {("B", "k"): 100_000_000, ("C", "k"): 100_000_000, ("D", "k"): 100_000_000, ("E", "k"): 100_000_000}
    today = {("B", "k"): 99_000_000, ("C", "k"): 85_000_000, ("D", "k"): 125_000_000, ("E", "k"): 40_000_000}
    alerts = {a["store"]: a for a in rules.price_change_alerts(today, previous, DAY)}
    assert "B" not in alerts
    assert (alerts["C"]["alert_type"], alerts["C"]["severity"]) == ("price_drop", "medium")
    assert (alerts["D"]["alert_type"], alerts["D"]["severity"]) == ("price_increase", "high")
    assert alerts["E"]["suspected_data_error"] and not alerts["C"]["suspected_data_error"]


def test_undercut_alerts_only_when_lead_is_lost():
    previous = {("FPT Shop", "k"): 100, ("B", "k"): 105, ("C", "k"): 95}
    today = {("FPT Shop", "k"): 100, ("B", "k"): 98, ("C", "k"): 94}
    alerts = rules.undercut_alerts(today, previous, DAY, "FPT Shop")
    assert [(a["store"], a["severity"]) for a in alerts] == [("B", "medium")]
