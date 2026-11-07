"""Runtime settings, read from environment variables or a local .env file."""
import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs"

load_dotenv(BASE_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def own_store() -> str:
    return os.getenv("OWN_STORE", "FPT Shop")


def postgres_url() -> str:
    user = quote_plus(_required("POSTGRES_USER"))
    password = quote_plus(_required("POSTGRES_PASSWORD"))
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{_required('POSTGRES_DB')}"


def mongo_uri() -> str:
    user = quote_plus(_required("MONGO_USER"))
    password = quote_plus(_required("MONGO_PASSWORD"))
    host = os.getenv("MONGO_HOST", "localhost")
    port = os.getenv("MONGO_PORT", "27017")
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin"
