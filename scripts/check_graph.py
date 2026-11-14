"""Audit the configured database for relationships that aren't genuine.

Usage:
    uv run python scripts/check_graph.py                         # test DB
    COLLECTIVIZE_ENV=production uv run python scripts/check_graph.py

Read-only: it reports problems and never changes data.
Exits 0 when the graph is clean, 1 when it finds problems.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.config import load_config
from backend.database import open_graph
from backend.objects import DomainRuleError

log = logging.getLogger("check_graph")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    if not config.path.exists():
        log.error("No database at %s.", config.path)
        return 1
    try:
        with open_graph(config) as graph:
            issues = graph.integrity.audit()
    except (DomainRuleError, ValueError) as error:
        log.error("A stored record breaks the current rules and can't be loaded: %s", error)
        return 1
    if not issues:
        log.info("%s database at %s: no problems found.", config.environment.value, config.path)
        return 0
    for issue in issues:
        log.info("%s  %s", issue.record_id, issue.problem)
    log.info("%d problem(s) found.", len(issues))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
