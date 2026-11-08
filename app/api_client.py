"""Thin HTTP client for the finalPricing API."""
import os
from typing import Any

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


class ApiError(RuntimeError):
    pass


def get_json(path: str, **params: Any) -> Any:
    try:
        response = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ApiError(str(exc)) from exc
    return response.json()


def post_json(path: str, payload: dict[str, Any]) -> Any:
    try:
        response = requests.post(f"{API_BASE_URL}{path}", json=payload, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ApiError(str(exc)) from exc
    return response.json()
