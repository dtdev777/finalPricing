"""Formatting and table helpers for the dashboard. Pure functions, no Streamlit calls."""
import re
from typing import Any

import pandas as pd

DONG = "\u20ab"

# Fixed store order, so a store keeps its chart color whatever the filters show.
STORES = ["FPT Shop", "CellphoneS", "TGDD", "Hoang Ha Mobile", "TopZone", "Viettel Store", "MobileCity", "Apple Store"]
STORE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER_COLOR = "#8a8a85"

# Diverging poles for "cheaper than market" and "dearer than market".
CHEAPER = "#2a78d6"
DEARER = "#e34948"

MATRIX_COLUMNS = {
    "product_match_key", "product", "series", "capacity_gb", "cheapest_store", "cheapest_price",
    "market_median", "store_count", "own_vs_cheapest", "own_vs_median_pct",
}

SEVERITY = {
    "high": ("red", ":material/error:"),
    "medium": ("orange", ":material/warning:"),
    "low": ("gray", ":material/info:"),
}


def vnd(value: Any) -> str:
    """18700000 -> '18.700.000' plus the dong sign, the way Vietnamese retailers write prices."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.0f}".replace(",", ".") + f" {DONG}"


def signed_vnd(value: float) -> str:
    return ("+" if value > 0 else "") + vnd(value)


def product_label(match_key: str) -> str:
    """'iphone-16-pro-max-256gb' -> 'iPhone 16 Pro Max 256GB'."""
    *model, capacity = match_key.split("-")
    name = " ".join(part.capitalize() for part in model).replace("Iphone", "iPhone")
    return f"{name} {capacity.upper()}"


def series(match_key: str) -> str:
    """Product line used by the filter: 'iphone-16-pro-max-256gb' -> 'iPhone 16'."""
    generation = match_key.split("-")[1]
    return "iPhone Air" if generation == "air" else f"iPhone {generation}"


def series_sort_key(name: str) -> tuple[int, str]:
    number = re.search(r"\d+", name)
    return (-int(number.group()) if number else 0, name)


def capacity_gb(match_key: str) -> int:
    capacity = match_key.rsplit("-", 1)[-1]
    size = int(re.sub(r"\D", "", capacity) or 0)
    return size * 1024 if capacity.endswith("tb") else size


def store_order(stores: list[str], own_store: str) -> list[str]:
    known = [store for store in STORES if store in stores and store != own_store]
    unknown = sorted(set(stores) - set(STORES) - {own_store})
    return ([own_store] if own_store in stores else []) + known + unknown


def store_columns(matrix: pd.DataFrame, own_store: str) -> list[str]:
    """The store price columns of a price_matrix() frame, reference store first."""
    return store_order([column for column in matrix.columns if column not in MATRIX_COLUMNS], own_store)


def store_colors(stores: list[str]) -> list[str]:
    return [STORE_COLORS[STORES.index(store)] if store in STORES else OTHER_COLOR for store in stores]


def price_matrix(products: list[dict[str, Any]], own_store: str) -> pd.DataFrame:
    """One row per product, one column per store (lowest listed price), plus market comparison columns."""
    if not products:
        return pd.DataFrame()
    frame = pd.DataFrame(products).dropna(subset=["product_match_key"])
    if frame.empty:
        return pd.DataFrame()
    wide = frame.pivot_table(index="product_match_key", columns="store", values="price", aggfunc="min")
    stores = store_order(list(wide.columns), own_store)
    wide = wide[stores]

    wide["cheapest_store"] = wide[stores].idxmin(axis=1)
    wide["cheapest_price"] = wide[stores].min(axis=1)
    wide["market_median"] = wide[stores].median(axis=1)
    wide["store_count"] = wide[stores].notna().sum(axis=1)
    if own_store in stores:
        wide["own_vs_cheapest"] = wide[own_store] - wide["cheapest_price"]
        wide["own_vs_median_pct"] = (wide[own_store] / wide["market_median"] - 1) * 100

    wide = wide.reset_index()
    wide["product"] = wide["product_match_key"].map(product_label)
    wide["series"] = wide["product_match_key"].map(series)
    wide["capacity_gb"] = wide["product_match_key"].map(capacity_gb)
    wide.columns.name = None
    order = sorted(wide.index, key=lambda row: product_sort_key(wide.at[row, "product_match_key"]))
    return wide.loc[order].reset_index(drop=True)


def product_sort_key(match_key: str) -> tuple:
    """Newest series first, then model name, then capacity."""
    return (series_sort_key(series(match_key)), match_key.rsplit("-", 1)[0], capacity_gb(match_key))


def position_summary(matrix: pd.DataFrame, own_store: str) -> dict[str, Any]:
    """Headline numbers for the reference store across the products it sells."""
    if matrix.empty or own_store not in matrix:
        return {"products": 0, "cheapest": 0, "above_median": 0, "price_index": None}
    own = matrix[matrix[own_store].notna() & (matrix["store_count"] > 1)]
    return {
        "products": len(own),
        "cheapest": int((own["own_vs_cheapest"] <= 0).sum()),
        "above_median": int((own["own_vs_median_pct"] > 0).sum()),
        "price_index": round(float((own[own_store] / own["market_median"]).mean() * 100), 1) if len(own) else None,
    }
