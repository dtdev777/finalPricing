import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.api import assistant
from src.matching import normalize_text, parse_capacity, parse_model, parse_store


@pytest.mark.parametrize(
    ("text", "model"),
    [
        ("iPhone 16 Pro Max 256GB", "iphone-16-pro-max"),
        ("iphone16promax256gb", "iphone-16-pro-max"),
        ("Giá iPhone 16e ở đâu rẻ nhất?", "iphone-16e"),
        ("iphone air 256gb", "iphone-air"),
        ("15 plus 128gb", "iphone-15-plus"),
        ("iPhone 17", "iphone-17"),
        ("dự báo ip16 128", "iphone-16"),
        ("16pm 256 bao nhiêu", "iphone-16-pro-max"),
        ("iPhone 16 promotion", "iphone-16"),
        ("128GB only", None),
        ("repair price", None),
    ],
)
def test_parse_model(text, model):
    assert parse_model(text) == model


@pytest.mark.parametrize(
    ("text", "capacity"),
    [
        ("iPhone 16 128GB", "128gb"),
        ("16promax256gb", "256gb"),
        ("17 Pro Max 1 TB", "1tb"),
        ("16 pro max 256 giá bao nhiêu", "256gb"),
        ("iPhone 16", None),
    ],
)
def test_parse_capacity(text, capacity):
    assert parse_capacity(text) == capacity


def test_parse_store_and_accents():
    assert normalize_text("Rẻ NHẤT ở Đâu") == "re nhat o dau"
    assert parse_store("giá ở Thế Giới Di Động") == "TGDD"
    assert parse_store("FPT có đắt hơn không") == "FPT Shop"
    assert parse_store("iPhone 16 128GB") is None


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("iPhone 16 128GB ở đâu rẻ nhất?", "cheapest"),
        ("So sánh giá iPhone 16 Pro 256GB", "compare"),
        ("FPT đắt hơn CellphoneS bao nhiêu với iPhone 16?", "compare"),
        ("Dự báo giá iPhone 16 128GB tuần tới", "forecast"),
        ("Có cảnh báo giá nào không?", "alerts"),
        ("iPhone 16 128GB", "compare"),
        ("hello", "help"),
    ],
)
def test_rules_pick_intent(question, intent):
    assert assistant.parse_with_rules(question).intent == intent


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.params = None

    def execute(self, _statement, params=None):
        self.params = params
        return FakeResult(self.rows)


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    monkeypatch.delenv("OPENAI_TOKEN_API_KEY", raising=False)
    monkeypatch.setenv("OWN_STORE", "FPT Shop")


def price_rows():
    day = date(2026, 11, 7)
    return [
        {"product_match_key": "iphone-16-128gb", "store": "CellphoneS", "crawl_date": day, "price": 18_700_000},
        {"product_match_key": "iphone-16-128gb", "store": "FPT Shop", "crawl_date": day, "price": 19_000_000},
    ]


# ---------------------------------------------------------------- rules engine


def test_cheapest_reports_gap_to_reference_store():
    conn = FakeConnection(price_rows())
    reply = assistant.ask(conn, "iPhone 16 128GB rẻ nhất ở đâu?")
    assert reply["engine"] == "rules"
    assert conn.params["pattern"] == "iphone-16-128gb"
    assert "cheapest at CellphoneS, 18,700,000 VND" in reply["answer"]
    assert "FPT Shop is 300,000 VND (1.6%) higher" in reply["answer"]


def test_compare_without_capacity_matches_all_capacities():
    conn = FakeConnection(price_rows())
    reply = assistant.ask(conn, "so sánh iPhone 16")
    assert conn.params["pattern"] == "iphone-16-[0-9]+(gb|tb)"
    assert "- CellphoneS: 18,700,000 VND (-300,000 VND vs FPT Shop)" in reply["answer"]


def test_cheapest_without_model_covers_all_products():
    conn = FakeConnection(price_rows())
    reply = assistant.ask(conn, "giá rẻ nhất ở đâu")
    assert conn.params["pattern"] == "iphone-[0-9a-z-]+-[0-9]+(gb|tb)"
    assert "cheapest at CellphoneS" in reply["answer"]


def test_no_data_gives_clear_answer():
    reply = assistant.ask(FakeConnection(), "iPhone 16 128GB rẻ nhất")
    assert reply["answer"] == "No prices found for this product yet."


# ------------------------------------------------------------------ LLM router


def tool_call(name, **args):
    return SimpleNamespace(id=f"call_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeOpenAI:
    """Returns scripted chat completions and records the messages it receives."""

    def __init__(self, *messages):
        self.script = list(messages)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def use_llm(monkeypatch, client):
    monkeypatch.setenv("OPENAI_TOKEN_API_KEY", "sk-test")
    monkeypatch.setattr(assistant, "_openai_client", lambda: client)


def test_router_calls_tool_and_answers_in_vietnamese(monkeypatch):
    client = FakeOpenAI(
        SimpleNamespace(content=None, tool_calls=[tool_call("latest_prices", model="iPhone 16", capacity="128", store=None)]),
        SimpleNamespace(content="Rẻ nhất là CellphoneS: 18.700.000đ, FPT Shop cao hơn 300.000đ.", tool_calls=None),
    )
    use_llm(monkeypatch, client)
    conn = FakeConnection(price_rows())

    reply = assistant.ask(conn, "con 16 thường 128 chỗ nào rẻ nhất?")

    assert reply["engine"] == "llm"
    assert reply["answer"].startswith("Rẻ nhất là CellphoneS")
    assert reply["tools"] == [{"name": "latest_prices", "args": {"model": "iPhone 16", "capacity": "128", "store": None}}]
    assert conn.params["pattern"] == "iphone-16-128gb"
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert '"diff_vs_reference": -300000' in tool_message["content"]


def test_router_rewrites_answer_once_after_invented_number(monkeypatch):
    client = FakeOpenAI(
        SimpleNamespace(content=None, tool_calls=[tool_call("latest_prices", model="iPhone 16", capacity="128GB", store=None)]),
        SimpleNamespace(content="Rẻ nhất là CellphoneS: 17.990.000đ.", tool_calls=None),
        SimpleNamespace(content="Rẻ nhất là CellphoneS: 18.700.000đ.", tool_calls=None),
    )
    use_llm(monkeypatch, client)

    reply = assistant.ask(FakeConnection(price_rows()), "iPhone 16 128GB rẻ nhất ở đâu?")

    assert reply["engine"] == "llm"
    assert reply["answer"] == "Rẻ nhất là CellphoneS: 18.700.000đ."
    assert "17990000" in client.requests[2]["messages"][-1]["content"]


def test_router_answer_with_invented_number_twice_falls_back_to_rules(monkeypatch):
    client = FakeOpenAI(
        SimpleNamespace(content=None, tool_calls=[tool_call("latest_prices", model="iPhone 16", capacity="128GB", store=None)]),
        SimpleNamespace(content="Rẻ nhất là CellphoneS: 17.990.000đ.", tool_calls=None),
        SimpleNamespace(content="Rẻ nhất là CellphoneS: 17.990.000đ.", tool_calls=None),
    )
    use_llm(monkeypatch, client)

    reply = assistant.ask(FakeConnection(price_rows()), "iPhone 16 128GB rẻ nhất ở đâu?")

    assert reply["engine"] == "rules"
    assert "cheapest at CellphoneS, 18,700,000 VND" in reply["answer"]


def test_router_error_falls_back_to_rules(monkeypatch):
    use_llm(monkeypatch, None)

    def broken(_conn, _question):
        raise RuntimeError("network down")

    monkeypatch.setattr(assistant, "route_with_llm", broken)
    reply = assistant.ask(FakeConnection(price_rows()), "iPhone 16 128GB rẻ nhất")
    assert reply["engine"] == "rules"


def test_run_tool_normalizes_and_bounds_arguments(monkeypatch):
    received = {}

    def fake_changes(_conn, **kwargs):
        received.update(kwargs)
        return []

    _, description, params = assistant.TOOL_SPECS["price_changes"]
    monkeypatch.setitem(assistant.TOOL_SPECS, "price_changes", (fake_changes, description, params))

    assistant.run_tool(None, "price_changes", {"model": "16 PM", "capacity": "256", "store": "Unknown", "days": 9999})

    assert received == {"model": "iphone-16-pro-max", "capacity": "256gb", "store": None, "days": 400}


def test_run_tool_rejects_unknown_tool():
    with pytest.raises(KeyError):
        assistant.run_tool(None, "drop_table", {})


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("CellphoneS: 18.700.000đ, FPT cao hơn 300.000đ (1,6%)", []),
        ("CellphoneS: 18,700,000 VND on 2026-11-07", []),
        ("CellphoneS: 17.990.000đ", [17_990_000]),
        ("No data yet.", []),
    ],
)
def test_ungrounded_numbers(answer, expected):
    results = [[{"price": 18_700_000, "diff_vs_reference": -300_000, "diff_pct_vs_reference": -1.58}]]
    assert assistant.ungrounded_numbers(answer, results) == expected
