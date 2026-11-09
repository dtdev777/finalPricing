import math

import pandas as pd
import pytest

from app import ui


@pytest.mark.parametrize(
    ("value", "text"),
    [(18_700_000, "18.700.000 \u20ab"), (18_700_000.0, "18.700.000 \u20ab"), (None, "-"), (math.nan, "-")],
)
def test_vnd(value, text):
    assert ui.vnd(value) == text


def test_signed_vnd():
    assert ui.signed_vnd(300_000) == "+300.000 \u20ab"
    assert ui.signed_vnd(-300_000) == "-300.000 \u20ab"


@pytest.mark.parametrize(
    ("key", "label", "series"),
    [
        ("iphone-16-pro-max-256gb", "iPhone 16 Pro Max 256GB", "iPhone 16"),
        ("iphone-16e-128gb", "iPhone 16e 128GB", "iPhone 16e"),
        ("iphone-air-256gb", "iPhone Air 256GB", "iPhone Air"),
        ("iphone-17-pro-max-1tb", "iPhone 17 Pro Max 1TB", "iPhone 17"),
    ],
)
def test_product_label_and_series(key, label, series):
    assert ui.product_label(key) == label
    assert ui.series(key) == series


def test_store_order_puts_reference_first_and_keeps_colors_fixed():
    stores = ui.store_order(["TGDD", "Unknown Shop", "CellphoneS", "FPT Shop"], "FPT Shop")
    assert stores == ["FPT Shop", "CellphoneS", "TGDD", "Unknown Shop"]
    assert ui.store_colors(["TGDD"]) == ui.store_colors(stores)[2:3]
    assert ui.store_colors(["Unknown Shop"]) == [ui.OTHER_COLOR]


def row(key, store, price):
    return {"product_match_key": key, "store": store, "price": price}


def test_price_matrix_and_position_summary():
    products = [
        row("iphone-16-128gb", "FPT Shop", 19_000_000),
        row("iphone-16-128gb", "CellphoneS", 18_700_000),
        row("iphone-16-128gb", "TGDD", 19_200_000),
        row("iphone-17-256gb", "FPT Shop", 24_000_000),
        row("iphone-17-256gb", "CellphoneS", 24_500_000),
        row("iphone-17-256gb", "CellphoneS", 24_400_000),
        row("iphone-15-128gb", "TGDD", 15_000_000),
    ]
    matrix = ui.price_matrix(products, "FPT Shop")

    assert list(matrix["product"]) == ["iPhone 17 256GB", "iPhone 16 128GB", "iPhone 15 128GB"]
    assert ui.store_columns(matrix, "FPT Shop") == ["FPT Shop", "CellphoneS", "TGDD"]
    iphone16 = matrix.set_index("product_match_key").loc["iphone-16-128gb"]
    assert iphone16["cheapest_store"] == "CellphoneS"
    assert iphone16["own_vs_cheapest"] == 300_000
    assert iphone16["own_vs_median_pct"] == pytest.approx(0.0)
    assert matrix.set_index("product_match_key").loc["iphone-17-256gb", "CellphoneS"] == 24_400_000

    summary = ui.position_summary(matrix, "FPT Shop")
    assert summary["products"] == 2
    assert summary["cheapest"] == 1
    assert summary["above_median"] == 0
    assert summary["price_index"] == pytest.approx(99.6, abs=0.05)


def test_gap_segments_start_a_new_segment_after_a_missing_day():
    history = pd.DataFrame({
        "store": ["A", "A", "A", "B", "A"],
        "crawl_date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-30", "2026-10-01", "2026-11-09"]),
    })
    assert list(ui.gap_segments(history)) == [0, 0, 1, 0, 2]


def test_price_matrix_empty():
    assert ui.price_matrix([], "FPT Shop").empty
    assert ui.position_summary(ui.price_matrix([], "FPT Shop"), "FPT Shop")["price_index"] is None
