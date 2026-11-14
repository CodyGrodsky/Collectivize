"""Try AI search from the terminal against the configured database.

Usage:
    uv run python scripts/ai_search.py --as Drew --near-campus "something fast paced"
    uv run python scripts/ai_search.py --as Kai --single "chill evening"

By default the search agent runs: Claude works out the goal, looks up what
matters about the person, runs its own searches and picks the best events
(a few API calls). --single uses one API call instead. Both cost a little.
Credentials: ANTHROPIC_API_KEY, the macOS Keychain (backend/credentials.py),
or an `ant auth login` profile. Without any, it falls back and says so.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.config import load_config
from backend.database import open_graph
from backend.objects import Person
from backend.objects.base import utc_now
from backend.seed import CAMPUS

log = logging.getLogger("ai_search")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request", help="what you're looking for, in your own words")
    parser.add_argument("--as", dest="viewer", help="first name of the person searching")
    parser.add_argument("--near-campus", action="store_true", help="search from campus centre")
    parser.add_argument("--radius", type=float, help="only events within this many km")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--single", action="store_true", help="one API call, no agent")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = load_config()
    if not config.path.exists():
        log.error("No database at %s. Seed it first.", config.path)
        return 1
    with open_graph(config) as graph:
        viewer_id = None
        if args.viewer:
            matches = [p for p in graph.store.nodes(Person)
                       if p.display_name.split(" ")[0].lower() == args.viewer.lower()]
            if len(matches) != 1:
                log.error("No single person named %r.", args.viewer)
                return 1
            viewer_id = matches[0].id
        origin = CAMPUS if args.near_campus or args.radius else None
        if args.single:
            result = graph.ai_search.search(
                viewer_id, args.request, utc_now(), origin=origin,
                max_distance_km=args.radius, limit=args.limit,
            )
            log.info("Understood as: %s", result.understood_as)
            log.info("Used AI: %s%s", result.used_ai, f" ({result.note})" if result.note else "")
            items = result.page.items
        else:
            result = graph.search_agent.search(viewer_id, args.request, utc_now(), origin=origin)
            log.info("Goal: %s", result.goal)
            log.info("Used agent: %s%s", result.used_agent,
                     f" ({result.note})" if result.note else "")
            log.info("Steps:")
            for step in result.steps:
                shown = {k: v for k, v in step.input.items() if v not in (None, [], "")}
                log.info("  - %s %s%s", step.tool, shown if shown else "",
                         "  [error]" if step.is_error else "")
            items = result.page.items
    log.info("Results:")
    for item in items:
        log.info("  %3.0f%%  %-26s %s", item.score * 100, item.event.title,
                 " | ".join(item.reasons[1:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
