"""Market position of every store's price against the other stores on the same day."""
import logging
from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

# Within this distance of the market median a price counts as "at market".
AT_MARKET_PCT = 1.0
COLUMNS = [
    "store", "product_match_key", "crawl_date", "price", "market_min", "market_max", "market_avg",
    "market_median", "price_diff", "price_diff_pct", "price_position", "price_index",
]


def price_position(price: float, low: float, high: float, median: float, stores: int) -> str:
    """One of six groups: only_store, cheapest, below_market, at_market, above_market, most_expensive."""
    if stores < 2:
        return "only_store"
    if price <= low:
        return "cheapest"
    if price >= high:
        return "most_expensive"
    gap = (price / median - 1) * 100
    if gap < -AT_MARKET_PCT:
        return "below_market"
    if gap > AT_MARKET_PCT:
        return "above_market"
    return "at_market"


def market_rows(prices: pd.DataFrame) -> pd.DataFrame:
    """prices: store, product_match_key, crawl_date, price (lowest per store and product)."""
    if prices.empty:
        return pd.DataFrame(columns=COLUMNS)
    market = prices.groupby(["product_match_key", "crawl_date"])["price"].agg(
        market_min="min", market_max="max", market_avg="mean", market_median="median", stores="count"
    )
    rows = prices.join(market, on=["product_match_key", "crawl_date"])
    rows["market_avg"] = rows["market_avg"].round(2)
    rows["price_diff"] = rows["price"] - rows["market_min"]
    rows["price_diff_pct"] = ((rows["price"] / rows["market_median"] - 1) * 100).round(3)
    rows["price_index"] = (rows["price"] / rows["market_median"] * 100).round(3)
    rows["price_position"] = [
        price_position(r.price, r.market_min, r.market_max, r.market_median, r.stores)
        for r in rows.itertuples()
    ]
    return rows[COLUMNS]


def run(engine: Engine, crawl_date: date) -> None:
    with engine.begin() as conn:
        prices = pd.DataFrame(
            conn.execute(
                text("""
                    SELECT store, product_match_key, crawl_date, min(price) AS price
                    FROM iphone_clean_products
                    WHERE crawl_date = :day AND product_match_key IS NOT NULL
                    GROUP BY store, product_match_key, crawl_date
                """),
                {"day": crawl_date},
            ).mappings().all()
        )
        rows = market_rows(prices)
        conn.execute(text("DELETE FROM iphone_price_analytics WHERE crawl_date = :day"), {"day": crawl_date})
        if not rows.empty:
            conn.execute(
                text(f"INSERT INTO iphone_price_analytics ({', '.join(COLUMNS)}) "
                     f"VALUES ({', '.join(':' + c for c in COLUMNS)})"),
                rows.to_dict("records"),
            )
    logger.info("Wrote %d market position rows", len(rows))
