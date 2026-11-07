-- Runs once, when the postgres_data volume is first created.

CREATE TABLE iphone_clean_products (
    store             TEXT        NOT NULL,
    product_id        TEXT        NOT NULL,
    crawl_date        DATE        NOT NULL,
    product_name      TEXT        NOT NULL,
    model             TEXT,
    capacity          TEXT,
    color             TEXT,
    price             BIGINT      NOT NULL CHECK (price BETWEEN 1000000 AND 120000000),
    original_price    BIGINT,
    in_stock          BOOLEAN,
    url               TEXT,
    product_match_key TEXT,
    data_origin       TEXT        NOT NULL CHECK (data_origin IN ('teacher_seed', 'own_homework', 'own_crawl')),
    crawled_at        TIMESTAMPTZ,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store, product_id, crawl_date)
);
CREATE INDEX idx_clean_match_key ON iphone_clean_products (product_match_key, crawl_date);
CREATE INDEX idx_clean_crawl_date ON iphone_clean_products (crawl_date);

-- Rejected rows stay here with the reason, unchanged, so rules can be re-run later.
CREATE TABLE iphone_quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    store         TEXT,
    crawl_date    DATE,
    reason        TEXT        NOT NULL,
    raw_record    JSONB       NOT NULL,
    data_origin   TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE iphone_price_analytics (
    store             TEXT    NOT NULL,
    product_match_key TEXT    NOT NULL,
    crawl_date        DATE    NOT NULL,
    price             BIGINT  NOT NULL,
    market_min        BIGINT,
    market_max        BIGINT,
    market_avg        NUMERIC(14, 2),
    market_median     NUMERIC(14, 2),
    price_diff        BIGINT,
    price_diff_pct    NUMERIC(8, 3),
    price_position    TEXT,
    price_index       NUMERIC(8, 3),
    PRIMARY KEY (store, product_match_key, crawl_date)
);

CREATE TABLE price_alerts (
    alert_id             BIGSERIAL PRIMARY KEY,
    crawl_date           DATE        NOT NULL,
    alert_type           TEXT        NOT NULL,
    severity             TEXT        NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    store                TEXT        NOT NULL,
    product_match_key    TEXT        NOT NULL,
    old_price            BIGINT,
    new_price            BIGINT,
    change_pct           NUMERIC(8, 3),
    reason               TEXT,
    suspected_data_error BOOLEAN     NOT NULL DEFAULT FALSE,
    acknowledged         BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alert_type, store, product_match_key, crawl_date)
);

CREATE TABLE price_forecasts (
    store             TEXT        NOT NULL,
    product_match_key TEXT        NOT NULL,
    forecast_date     DATE        NOT NULL,
    model_name        TEXT        NOT NULL,
    predicted_price   BIGINT      NOT NULL,
    lower_bound       BIGINT,
    upper_bound       BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store, product_match_key, forecast_date, model_name)
);

CREATE TABLE pipeline_runs (
    run_id      BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT        NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    message     TEXT
);
