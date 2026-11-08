"""Price assistant with two engines over the same read-only queries (src/api/queries.py).

- Rules: keywords pick a query and a fixed English template formats the answer. Always available.
- LLM router: when OPENAI_TOKEN_API_KEY is set, the model chooses which queries to call, then
  writes the answer in the question's language. Every number in that answer must appear in the
  query results; otherwise, or on any error, the rules answer is returned instead.
"""
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Connection

from config.settings import own_store
from src.api import queries
from src.matching import STORE_ALIASES, normalize_text, parse_capacity, parse_model, parse_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- rules engine

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
MAX_PRODUCTS = 10


@dataclass
class Query:
    intent: str
    model: str | None = None
    capacity: str | None = None
    store: str | None = None


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


def vnd(value: int | float) -> str:
    return f"{value:,.0f} VND"


def label(match_key: str) -> str:
    *model, capacity = match_key.split("-")
    name = " ".join(part.capitalize() for part in model).replace("Iphone", "iPhone")
    return f"{name} {capacity.upper()}"


def _by_product(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["product_match_key"], []).append(row)
    return list(grouped.items())[:MAX_PRODUCTS]


def cheapest_text(conn: Connection, query: Query) -> tuple[str, list[dict[str, Any]]]:
    reference = query.store or own_store()
    rows = queries.latest_prices(conn, query.model, query.capacity, reference=reference)
    lines = []
    for key, stores in _by_product(rows):
        best = stores[0]
        line = f"{label(key)}: cheapest at {best['store']}, {vnd(best['price'])} ({best['crawl_date']})."
        ref = next((s for s in stores if s["store"] == reference), None)
        if ref and ref["store"] != best["store"]:
            gap = ref["price"] - best["price"]
            line += f" {reference} is {vnd(gap)} ({gap / best['price']:.1%}) higher."
        lines.append(line)
    return "\n".join(lines), rows


def compare_text(conn: Connection, query: Query) -> tuple[str, list[dict[str, Any]]]:
    reference = query.store or own_store()
    rows = queries.latest_prices(conn, query.model, query.capacity, reference=reference)
    lines = []
    for key, stores in _by_product(rows):
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
    return "\n".join(lines), rows


def forecast_text(conn: Connection, query: Query) -> tuple[str, list[dict[str, Any]]]:
    store = query.store or own_store()
    rows = queries.forecasts(conn, query.model, query.capacity, store)
    if not rows:
        return f"No forecast stored yet for this product at {store}.", []
    lines = [f"Forecast at {store}:"]
    for row in rows:
        line = f"- {label(row['product_match_key'])}, {row['forecast_date']}: {vnd(row['predicted_price'])}"
        if row["lower_bound"] is not None and row["upper_bound"] is not None:
            line += f" (range {vnd(row['lower_bound'])} to {vnd(row['upper_bound'])})"
        lines.append(f"{line} [{row['model_name']}]")
    return "\n".join(lines), rows


def alerts_text(conn: Connection, query: Query) -> tuple[str, list[dict[str, Any]]]:
    rows = queries.alerts(conn, query.model, query.capacity, query.store, limit=5)
    if not rows:
        return "No price alerts match this question.", []
    lines = ["Latest alerts:"]
    for row in rows:
        line = f"- [{row['severity']}] {row['crawl_date']} {row['store']} {label(row['product_match_key'])}"
        if row["old_price"] is not None and row["new_price"] is not None:
            line += f": {vnd(row['old_price'])} -> {vnd(row['new_price'])}"
        if row["change_pct"] is not None:
            line += f" ({row['change_pct']:+.1f}%)"
        lines.append(line)
    return "\n".join(lines), rows


RULE_HANDLERS: dict[str, Callable[[Connection, Query], tuple[str, list[dict[str, Any]]]]] = {
    "cheapest": cheapest_text,
    "compare": compare_text,
    "forecast": forecast_text,
    "alerts": alerts_text,
}


def answer_with_rules(conn: Connection, question: str) -> dict[str, Any]:
    query = parse_with_rules(question)
    if query.intent == "help":
        text, rows = HELP_TEXT, []
    else:
        text, rows = RULE_HANDLERS[query.intent](conn, query)
        text = text or "No prices found for this product yet."
    return {"answer": text, "engine": "rules", "query": asdict(query), "tools": [query.intent], "data": rows}


# ----------------------------------------------------------------- LLM router

SEVERITIES = ("high", "medium", "low")
DEFAULT_DAYS = {"price_history": 30, "price_changes": 7}
MAX_TOOL_ROUNDS = 4

_MODEL_PARAM = {
    "type": ["string", "null"],
    "description": "iPhone model, e.g. 'iPhone 16', 'iPhone 16 Pro Max', 'iPhone 16e', 'iPhone Air'. Null for all models.",
}
_CAPACITY_PARAM = {"type": ["string", "null"], "description": "Storage, e.g. '128GB' or '1TB'. Null for all."}
_STORE_PARAM = {
    "type": ["string", "null"],
    "enum": [*STORE_ALIASES, None],
    "description": "Store name. Null for all stores (or the reference store where noted).",
}
_DAYS_PARAM = {"type": ["integer", "null"], "description": "Look-back window in days. Null for the default."}
_SEVERITY_PARAM = {"type": ["string", "null"], "enum": [*SEVERITIES, None]}

TOOL_SPECS: dict[str, tuple[Callable[..., Any], str, dict[str, Any]]] = {
    "latest_prices": (
        queries.latest_prices,
        "Latest price of each product at each store, cheapest first per product. Includes rank, "
        "the cheapest price, the gap above the cheapest, and the difference vs the reference store. "
        "Use for current price, cheapest store, and store comparison questions.",
        {"model": _MODEL_PARAM, "capacity": _CAPACITY_PARAM, "store": _STORE_PARAM},
    ),
    "price_history": (
        queries.price_history,
        "Price trend per store and product over a window (default 30 days): first, last, lowest and "
        "highest price and the change. Use for trend and 'has the price dropped' questions.",
        {"model": _MODEL_PARAM, "capacity": _CAPACITY_PARAM, "store": _STORE_PARAM, "days": _DAYS_PARAM},
    ),
    "price_changes": (
        queries.price_changes,
        "Products whose price changed over a window (default 7 days), biggest change first. "
        "Use for 'which phones dropped or raised prices' questions.",
        {"model": _MODEL_PARAM, "capacity": _CAPACITY_PARAM, "store": _STORE_PARAM, "days": _DAYS_PARAM},
    ),
    "forecasts": (
        queries.forecasts,
        "Stored price forecasts. Store defaults to the reference store.",
        {"model": _MODEL_PARAM, "capacity": _CAPACITY_PARAM, "store": _STORE_PARAM},
    ),
    "alerts": (
        queries.alerts,
        "Latest price alerts, most severe first.",
        {"model": _MODEL_PARAM, "capacity": _CAPACITY_PARAM, "store": _STORE_PARAM, "severity": _SEVERITY_PARAM},
    ),
    "market_position": (
        queries.market_position,
        "Price position and price index of each product at a store vs the market. Store defaults to the reference store.",
        {"store": _STORE_PARAM},
    ),
    "catalog": (
        queries.catalog,
        "Products available in the data, with the number of stores and the date range. "
        "Use when the question asks what models or data exist.",
        {},
    ),
}

ROUTER_PROMPT = """You assist the pricing team of {store}, an iPhone retailer in Vietnam. The user works at {store}; they are not a shopper.
"We", "us", "our store", "minh", "ben minh" and "chung ta" all mean {store}. A positive diff_vs_reference means that store is more expensive than {store}.

Answer only from tool results:
- Call tools before stating any price, date or number. Call several tools if needed.
- If no store is mentioned, look at all stores; do not limit the query to {store}.
- Quote numbers exactly as the tools return them, including differences and percentages. Do not calculate new numbers.
- If the tools return no rows, say there is no data for that yet.
- If the question is not about iPhone prices, say in one sentence that you only answer iPhone price questions and give two example questions. Do not ask follow-up questions about other topics.

Style:
- Reply in the language of the question.
- Format every price with thousands separators: 18.700.000đ in Vietnamese, 18,700,000 VND in English.
- Use the "product" field for product names. Never show raw field names or codes such as change_pct or slightly_above_market; describe them in plain words.
- Start with the direct answer, then at most 5 short bullet points. Mention the data date.
Vietnamese hints: "thuong" or "ban thuong" means the base model, "pm" means Pro Max, "ip" means iPhone."""


def llm_enabled() -> bool:
    return bool(os.getenv("OPENAI_TOKEN_API_KEY"))


def _openai_client():
    from openai import OpenAI

    return OpenAI(api_key=os.environ["OPENAI_TOKEN_API_KEY"], timeout=30, max_retries=1)


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": list(params),
                    "additionalProperties": False,
                },
            },
        }
        for name, (_, description, params) in TOOL_SPECS.items()
    ]


def run_tool(conn: Connection, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the LLM's arguments with the same parsers as the rules, then run the query."""
    function, _, params = TOOL_SPECS[name]
    kwargs: dict[str, Any] = {}
    if "model" in params:
        kwargs["model"] = parse_model(args.get("model") or "")
    if "capacity" in params:
        kwargs["capacity"] = parse_capacity(args.get("capacity") or "")
    if "store" in params:
        kwargs["store"] = args.get("store") if args.get("store") in STORE_ALIASES else None
    if "days" in params:
        kwargs["days"] = min(max(int(args.get("days") or DEFAULT_DAYS[name]), 1), 400)
    if "severity" in params:
        kwargs["severity"] = args.get("severity") if args.get("severity") in SEVERITIES else None
    rows = function(conn, **kwargs)
    return [{"product": label(row["product_match_key"]), **row} if "product_match_key" in row else row for row in rows]


_LARGE_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d{5,}")


def ungrounded_numbers(answer: str, results: list[Any]) -> list[int]:
    """Numbers of 10,000 or more in the answer that do not appear in any query result."""
    allowed: set[int] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(abs(round(value)))

    collect(results)
    found = (int(re.sub(r"[.,]", "", n)) for n in _LARGE_NUMBER.findall(answer))
    return [n for n in found if n >= 10_000 and n not in allowed]


def route_with_llm(conn: Connection, question: str) -> dict[str, Any]:
    client = _openai_client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ROUTER_PROMPT.format(store=own_store())},
        {"role": "user", "content": question},
    ]
    calls: list[dict[str, Any]] = []
    corrected = False
    for _ in range(MAX_TOOL_ROUNDS):
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL") or "gpt-5.4-mini",
            messages=messages,
            tools=tool_definitions(),
        )
        message = completion.choices[0].message
        if not message.tool_calls:
            answer = (message.content or "").strip()
            if not answer:
                raise ValueError("Empty LLM answer")
            if bad := ungrounded_numbers(answer, [c["rows"] for c in calls]):
                if corrected:
                    raise ValueError(f"Answer has numbers not found in query results: {bad}")
                # One retry: point out the invented numbers and ask for a rewrite.
                corrected = True
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": f"These numbers are not in the tool results: {bad}. "
                        "Rewrite the answer using only numbers from the tool results.",
                    }
                )
                continue
            return {
                "answer": answer,
                "engine": "llm",
                "query": None,
                "tools": [{"name": c["name"], "args": c["args"]} for c in calls],
                "data": [row for c in calls for row in c["rows"]],
            }

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        )
        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            rows = run_tool(conn, call.function.name, args)
            calls.append({"name": call.function.name, "args": args, "rows": rows})
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(rows, default=str, ensure_ascii=False)}
            )
    raise RuntimeError("LLM did not answer within the tool-call limit")


def ask(conn: Connection, question: str) -> dict[str, Any]:
    if llm_enabled():
        try:
            return route_with_llm(conn, question)
        except Exception:
            logger.exception("LLM router failed, falling back to rules")
    return answer_with_rules(conn, question)
