FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

COPY config ./config
COPY src ./src
COPY app ./app
COPY .streamlit ./.streamlit

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p data logs \
    && chown -R appuser:appuser /app
USER appuser
