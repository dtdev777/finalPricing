"""Daily pipeline entry point. Add each new stage to STAGES in execution order."""
import logging
import sys
from collections.abc import Callable
from datetime import date

from pymongo import MongoClient
from sqlalchemy import create_engine, text

from config.settings import LOG_DIR, mongo_uri, postgres_url

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


def check_mongo() -> None:
    with MongoClient(mongo_uri(), serverSelectionTimeoutMS=5000) as client:
        client.admin.command("ping")


STAGES: list[tuple[str, Callable[[], None]]] = [
    ("check_mongo", check_mongo),
]


def main() -> int:
    configure_logging()
    engine = create_engine(postgres_url())

    with engine.begin() as conn:
        run_id = conn.execute(
            text("INSERT INTO pipeline_runs (status) VALUES ('running') RETURNING run_id")
        ).scalar_one()

    status, message = "success", f"Completed {len(STAGES)} stage(s)"
    try:
        for name, stage in STAGES:
            logger.info("Starting stage %s", name)
            stage()
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
