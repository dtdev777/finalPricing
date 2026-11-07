"""PostgreSQL access for the API."""
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from config.settings import postgres_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(postgres_url(), pool_pre_ping=True)


def get_connection() -> Iterator[Connection]:
    with get_engine().connect() as conn:
        yield conn
