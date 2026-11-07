from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.api.db import get_connection
from src.api.main import app


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.params = None

    def execute(self, _statement, params=None):
        if self.error:
            raise self.error
        self.params = params
        return FakeResult(self.rows)


def client_for(connection):
    app.dependency_overrides[get_connection] = lambda: connection
    return TestClient(app)


def test_health_returns_ok_when_database_responds():
    response = client_for(FakeConnection()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_database_is_down():
    error = OperationalError("SELECT 1", {}, Exception("connection refused"))
    response = client_for(FakeConnection(error=error)).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_kpis_default_to_the_reference_store(monkeypatch):
    monkeypatch.setenv("OWN_STORE", "FPT Shop")
    connection = FakeConnection([{"latest_crawl_date": date(2026, 11, 7), "products_tracked": 3}])
    response = client_for(connection).get("/api/v1/kpis")
    assert response.status_code == 200
    assert response.json()["latest_crawl_date"] == "2026-11-07"
    assert connection.params["store"] == "FPT Shop"


def test_numeric_columns_are_returned_as_numbers():
    connection = FakeConnection([{"avg_price_index": Decimal("99.50")}])
    response = client_for(connection).get("/api/v1/kpis")
    assert response.json()["avg_price_index"] == 99.5


def test_alerts_reject_unknown_severity():
    response = client_for(FakeConnection()).get("/api/v1/alerts", params={"severity": "urgent"})
    assert response.status_code == 422


def test_products_reject_limit_above_maximum():
    response = client_for(FakeConnection()).get("/api/v1/products", params={"limit": 1001})
    assert response.status_code == 422
