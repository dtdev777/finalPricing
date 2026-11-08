"""Price assistant: turns a question into one of a few fixed queries.

Rules always work. When OPENAI_TOKEN_API_KEY is set, an LLM parses the question
instead, but it only fills in the query fields; every number in the answer comes
from PostgreSQL. Any LLM failure falls back to the rules.
"""
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Connection, text

from config.settings import own_store
from src.matching import STORE_ALIASES, build_match_key, normalize_text, parse_capacity, parse_model, parse_store

logger = logging.getLogger(__name__)

INTENTS = ("cheapest", "compare", "forecast", "alerts", "help")

# English and Vietnamese keywords (accents removed), checked in this order.
INTENT_KEYWORDS = {
    "forecast": ("forecast", "predict", "du bao", "du doan", "tuan toi", "ngay mai", "sap toi"),
    "alerts": ("alert", "warning", "canh bao", "bien dong", "doi gia"),
    "cheapest": ("cheapest", "lowest", "re nhat", "thap nhat", "o dau re"),
    "compare": ("compare", "versus", " vs ", "so sanh", "chenh lech", "dat hon", "re hon"),
}

HELP_TEXT = (
    "I can answer four kinds of questions about iPhone prices:\n"
    "- Cheapest store: 'Where is iPhone 16 128GB cheapest?'\n"
    "- Compare stores: 'Compare iPhone 16 Pro 256GB prices'\n"
    "- Forecast: 'Forecast for iPhone 16 128GB at FPT Shop'\n"
    "- Alerts: 'Latest price alerts'"
)


@dataclass
class Query:
    intent: str
    model: str | None = None
    capacity: str | None = None
    store: str | None = None


@dataclass
class Answer:
    text: str
    data: list[dict[str, Any]]


def parse_with_rules(question: str) -> Query:
    normalized = f" {normalize_text(question)} "
    intent = next(
        (name for name, words in INTENT_KEYWORDS.items() if any(w in normalized for w in words)),
        None,
    )
    model = parse_model(question)
    if intent is None:
        intent = "compare" if model else "help"
    return Query(intent, model, parse_capacity(question), parse_store(question))


LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "model", "capacity", "store"],
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "model": {"type": ["string", "null"], "description": "iPhone model, e.g. 'iPhone 16 Pro Max'"},
        "capacity": {"type": ["string", "null"], "description": "Storage, e.g. '256GB' or '1TB'"},
        "store": {"type": ["string", "null"], "enum": [*STORE_ALIASES, None]},
    },
}

LLM_PROMPT = (
    "You route questions about iPhone prices in Vietnamese retail stores. "
    "Questions may be in Vietnamese or English. Pick the intent: cheapest (which store is cheapest), "
    "compare (the current price of a model, prices across stores, or how much more expensive a store is), "
    "forecast (future price), alerts (recent price changes or warnings), "
    "help (not about iPhone prices). "
    "Extract the model, capacity and store only if the question mentions them."
)


def llm_enabled() -> bool:
    return bool(os.getenv("OPENAI_TOKEN_API_KEY"))


def parse_with_llm(question: str) -> Query:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_TOKEN_API_KEY"], timeout=20, max_retries=1)
    completion = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL") or "gpt-5.4-nano",
        messages=[{"role": "system", "content": LLM_PROMPT}, {"role": "user", "content": question}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "price_query", "strict": True, "schema": LLM_SCHEMA},
        },
    )
    fields = json.loads(completion.choices[0].message.content)
    # Re-parse the LLM's fields with the same rules so keys match the database format.
    return Query(
        intent=fields["intent"] if fields["intent"] in INTENTS else "help",
        model=parse_model(fields["model"] or ""),
        capacity=parse_capacity(fields["capacity"] or ""),
        store=fields["store"] if fields["store"] in STORE_ALIASES else None,
    )


def vnd(value: int | float) -> str:
    return f"{value:,.0f} VND"


def label(match_key: str) -> str:
    *model, capacity = match_key.split("-")
    name = " ".join(part.capitalize() for part in model).replace("Iphone", "iPhone")
    return f"{name} {capacity.upper()}"


def _fetch(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(sql), params).mappings().all()]


def latest_prices(conn: Connection, query: Query) -> list[dict[str, Any]]:
    """Lowest price per store and product on each product's latest crawl date."""
    pattern = build_match_key(query.model, query.capacity or "[0-9]+(gb|tb)")
    return _fetch(
        conn,
        """
        WITH latest AS (
            SELECT product_match_key, max(crawl_date) AS crawl_date
            FROM iphone_clean_products
            WHERE product_match_key ~ ('^' || :pattern || '$')
            GROUP BY product_match_key
        )
        SELECT p.product_match_key, p.store, p.crawl_date, min(p.price) AS price
        FROM iphone_clean_products p
        JOIN latest l USING (product_match_key, crawl_date)
        GROUP BY p.product_match_key, p.store, p.crawl_date
        ORDER BY p.product_match_key, price
        """,
        pattern=pattern,
    )


def _by_product(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["product_match_key"], []).append(row)
    return grouped


def cheapest(conn: Connection, query: Query) -> Answer:
    rows = latest_prices(conn, query)
    reference = query.store or own_store()
    lines = []
    for key, stores in _by_product(rows).items():
        best = stores[0]
        line = f"{label(key)}: cheapest at {best['store']}, {vnd(best['price'])} ({best['crawl_date']})."
        ref = next((s for s in stores if s["store"] == reference), None)
        if ref and ref["store"] != best["store"]:
            gap = ref["price"] - best["price"]
            line += f" {reference} is {vnd(gap)} ({gap / best['price']:.1%}) higher."
        lines.append(line)
    return Answer("\n".join(lines), rows)


def compare(conn: Connection, query: Query) -> Answer:
    rows = latest_prices(conn, query)
    reference = query.store or own_store()
    lines = []
    for key, stores in _by_product(rows).items():
        lines.append(f"{label(key)} ({stores[0]['crawl_date']}):")
        ref = next((s for s in stores if s["store"] == reference), None)
        for row in stores:
            line = f"- {row['store']}: {vnd(row['price'])}"
            if ref and row is not ref:
                diff = row["price"] - ref["price"]
                line += f" ({'+' if diff >= 0 else '-'}{vnd(abs(diff))} vs {reference})"
            lines.append(line)
        if ref is None:
            lines.append(f"- No {reference} price for this product.")
    return Answer("\n".join(lines), rows)


def forecast(conn: Connection, query: Query) -> Answer:
    store = query.store or own_store()
    rows = _fetch(
        conn,
        """
        SELECT product_match_key, store, forecast_date, model_name, predicted_price, lower_bound, upper_bound
        FROM price_forecasts
        WHERE product_match_key ~ ('^' || :pattern || '$') AND store = :store
        ORDER BY product_match_key, forecast_date, model_name
        """,
        pattern=build_match_key(query.model, query.capacity or "[0-9]+(gb|tb)"),
        store=store,
    )
    if not rows:
        return Answer(f"No forecast stored yet for this product at {store}.", [])
    lines = [f"Forecast at {store}:"]
    for row in rows:
        line = f"- {label(row['product_match_key'])}, {row['forecast_date']}: {vnd(row['predicted_price'])}"
        if row["lower_bound"] is not None and row["upper_bound"] is not None:
            line += f" (range {vnd(row['lower_bound'])} to {vnd(row['upper_bound'])})"
        lines.append(f"{line} [{row['model_name']}]")
    return Answer("\n".join(lines), rows)


def alerts(conn: Connection, query: Query) -> Answer:
    pattern = build_match_key(query.model, query.capacity or "[0-9]+(gb|tb)") if query.model else ".*"
    rows = _fetch(
        conn,
        """
        SELECT crawl_date, severity, alert_type, store, product_match_key, old_price, new_price, change_pct, reason
        FROM price_alerts
        WHERE product_match_key ~ ('^' || :pattern || '$')
          AND (CAST(:store AS text) IS NULL OR store = :store)
        ORDER BY crawl_date DESC, CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
        LIMIT 5
        """,
        pattern=pattern,
        store=query.store,
    )
    if not rows:
        return Answer("No price alerts match this question.", [])
    lines = ["Latest alerts:"]
    for row in rows:
        line = f"- [{row['severity']}] {row['crawl_date']} {row['store']} {label(row['product_match_key'])}"
        if row["old_price"] is not None and row["new_price"] is not None:
            line += f": {vnd(row['old_price'])} -> {vnd(row['new_price'])}"
        if row["change_pct"] is not None:
            line += f" ({float(row['change_pct']):+.1f}%)"
        lines.append(line)
    return Answer("\n".join(lines), rows)


HANDLERS: dict[str, Callable[[Connection, Query], Answer]] = {
    "cheapest": cheapest,
    "compare": compare,
    "forecast": forecast,
    "alerts": alerts,
}
NEEDS_MODEL = {"cheapest", "compare", "forecast"}


def ask(conn: Connection, question: str) -> dict[str, Any]:
    query, engine = None, "rules"
    if llm_enabled():
        try:
            query, engine = parse_with_llm(question), "llm"
        except Exception:
            logger.exception("LLM parsing failed, falling back to rules")
    if query is None:
        query = parse_with_rules(question)
    if query.intent == "help" and query.model:
        query.intent = "compare"

    if query.intent == "help":
        result = Answer(HELP_TEXT, [])
    elif query.intent in NEEDS_MODEL and not query.model:
        result = Answer("Which iPhone model? For example: 'iPhone 16 Pro 256GB'.", [])
    else:
        result = HANDLERS[query.intent](conn, query)
        if not result.text:
            result = Answer("No prices found for this product yet.", [])
    return {"answer": result.text, "engine": engine, "query": asdict(query), "data": result.data}
