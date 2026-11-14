"""Run the local demo server: the frontend pages plus the JSON API.

Usage:
    uv run python scripts/serve.py              # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai      # search by keywords, no API calls
    uv run python scripts/serve.py --port 8080

Uses the test (demo) database; seed it first with scripts/seed_test_db.py.
With AI on, each new search makes a few Claude calls (a few cents); repeated
searches are answered from a cache.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.config import load_config
from backend.server import DiscoverApi, demo_graph_factory, serve

log = logging.getLogger("serve")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-ai", action="store_true", help="keyword search only, no API calls")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = load_config()
    if not config.path.exists():
        log.error("No database at %s. Run scripts/seed_test_db.py first.", config.path)
        return 1
    try:
        graphs = demo_graph_factory(config, use_ai=not args.no_ai)
    except PermissionError as error:
        log.error("%s", error)
        return 1
    server = serve(DiscoverApi(graphs), args.port)
    log.info("Collectivize demo on http://127.0.0.1:%d/  (Discover: /src/frontend/discover.html)",
             args.port)
    log.info("AI search: %s", "off (keywords only)" if args.no_ai else "on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
