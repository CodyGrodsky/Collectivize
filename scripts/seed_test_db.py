"""Fill the test database with fictional mock data.

Usage:
    uv run python scripts/seed_test_db.py           # create data/test.db
    uv run python scripts/seed_test_db.py --reset   # wipe and re-seed it

Refuses to run when COLLECTIVIZE_ENV=production.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.config import load_config
from backend.database import open_graph
from backend.graph.sqlite_store import (
    EnvironmentMismatchError,
    SqliteGraphStore,
)
from backend.objects.base import utc_now
from backend.seed import seed_mock_data

log = logging.getLogger("seed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete and re-seed the test DB")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = load_config()
    if config.is_production:
        log.error("Refusing to seed: COLLECTIVIZE_ENV is production.")
        return 1
    if config.path.exists():
        if not args.reset:
            log.error("%s already exists. Use --reset to wipe and re-seed it.", config.path)
            return 1
        try:  # only ever delete a file stamped as a test database
            SqliteGraphStore(config.path, config.environment).close()
        except EnvironmentMismatchError as error:
            log.error("Refusing to reset %s: %s", config.path, error)
            return 1
        config.path.unlink()

    with open_graph(config) as graph:
        result = seed_mock_data(graph, config.environment, utc_now())
    log.info(
        "Seeded %s: %d orgs, %d people, %d tags, %d events.",
        config.path,
        len(result.orgs),
        len(result.people),
        len(result.tags),
        len(result.events),
    )
    log.info("Demo invite code (from Drew): %s", result.invite_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
