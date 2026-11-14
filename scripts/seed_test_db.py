"""Fill the test database with fictional mock data.

Usage:
    uv run python scripts/seed_test_db.py                  # create data/test.db
    uv run python scripts/seed_test_db.py --reset          # wipe and re-seed it
    uv run python scripts/seed_test_db.py --size small     # smaller generated world
    uv run python scripts/seed_test_db.py --size demo      # hand-built demo only

By default the hand-built demo is joined by a large generated world
(three campuses, ~90 orgs, ~500 people, months of events and RSVPs).

Refuses to run when COLLECTIVIZE_ENV=production.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.config import Environment, load_config
from backend.database import open_graph
from backend.graph.sqlite_store import read_environment_stamp
from backend.objects.base import utc_now
from backend.seed import seed_mock_data
from backend.seed_bulk import LARGE, SMALL

SIZES = {"large": LARGE, "small": SMALL, "demo": None}

log = logging.getLogger("seed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete and re-seed the test DB")
    parser.add_argument("--size", choices=SIZES, default="large",
                        help="how much generated data to add to the hand-built demo")
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
        # Only ever delete a file already stamped as a test database. Read the
        # stamp without opening the store, which would stamp an unstamped file.
        stamp = read_environment_stamp(config.path)
        if stamp != Environment.TEST.value:
            log.error("Refusing to reset %s: it is not a test database (stamp: %s).",
                      config.path, stamp or "none")
            return 1
        config.path.unlink()

    with open_graph(config) as graph:
        result = seed_mock_data(graph, config.environment, utc_now(), SIZES[args.size])
    bulk = result.bulk
    log.info(
        "Seeded %s: %d orgs, %d people, %d tags, %d events.",
        config.path,
        len(result.orgs) + (len(bulk.orgs) if bulk else 0),
        len(result.people) + (len(bulk.people) if bulk else 0),
        len(bulk.tags) if bulk else len(result.tags),
        len(result.events) + (len(bulk.events) if bulk else 0),
    )
    log.info("Demo invite code (from Drew): %s", result.invite_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
