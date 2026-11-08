from datetime import date

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


def test_missing_model_asks_for_it():
    reply = assistant.ask(FakeConnection(), "giá rẻ nhất ở đâu")
    assert reply["answer"].startswith("Which iPhone model?")


def test_no_data_gives_clear_answer():
    reply = assistant.ask(FakeConnection(), "iPhone 16 128GB rẻ nhất")
    assert reply["answer"] == "No prices found for this product yet."


def test_llm_help_with_a_model_shows_prices(monkeypatch):
    monkeypatch.setenv("OPENAI_TOKEN_API_KEY", "sk-test")
    monkeypatch.setattr(assistant, "parse_with_llm", lambda _q: assistant.Query("help", "iphone-16", "128gb"))
    reply = assistant.ask(FakeConnection(price_rows()), "16 128 giá bao nhiêu")
    assert reply["engine"] == "llm"
    assert reply["query"]["intent"] == "compare"
    assert "- CellphoneS: 18,700,000 VND" in reply["answer"]


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("OPENAI_TOKEN_API_KEY", "sk-test")

    def broken(_question):
        raise RuntimeError("network down")

    monkeypatch.setattr(assistant, "parse_with_llm", broken)
    reply = assistant.ask(FakeConnection(price_rows()), "iPhone 16 128GB rẻ nhất")
    assert reply["engine"] == "rules"
    assert "cheapest at CellphoneS" in reply["answer"]
