"""Daily pipeline entry point. Add each new stage to STAGES in execution order."""
import logging
import sys
from collections.abc import Callable
from datetime import date

from sqlalchemy import Engine, create_engine, text

from config.settings import LOG_DIR, postgres_url
from src.alerts import rules
from src.pipeline import analytics, clean, crawl

logger = logging.getLogger("pipeline")


def configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / f"pipeline_{date.today():%Y-%m-%d}.log", encoding="utf-8"),
        ],
    )


# Each stage gets the database engine and the crawl date. A failing stage stops the run,
# so later stages never build on incomplete data.
STAGES: list[tuple[str, Callable[[Engine, date], None]]] = [
    ("crawl", crawl.run),
    ("clean_load", clean.run),
    ("analytics", analytics.run),
    ("alerts", rules.run),
]


def main() -> int:
    configure_logging()
    engine = create_engine(postgres_url())

    with engine.begin() as conn:
        run_id = conn.execute(
            text("INSERT INTO pipeline_runs (status) VALUES ('running') RETURNING run_id")
        ).scalar_one()

    # The container runs on Vietnam time, so this is the Vietnamese market day.
    crawl_date = date.today()
    status, message = "success", f"Completed {len(STAGES)} stage(s) for {crawl_date}"
    try:
        for name, stage in STAGES:
            logger.info("Starting stage %s", name)
            stage(engine, crawl_date)
            logger.info("Finished stage %s", name)
    except Exception as exc:
        logger.exception("Pipeline failed")
        status, message = "failed", f"{type(exc).__name__}: {exc}"

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE pipeline_runs SET status = :status, message = :message, "
                "finished_at = now() WHERE run_id = :run_id"
            ),
            {"status": status, "message": message, "run_id": run_id},
        )
    logger.info("Run %s: %s", run_id, status)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
