"""Run the local demo server: the frontend pages plus the JSON API.

Usage:
    uv run python scripts/serve.py              # http://127.0.0.1:8000/
    uv run python scripts/serve.py --no-ai      # search by keywords, no API calls
    uv run python scripts/serve.py --port 8080
    uv run python scripts/serve.py --no-login   # skip sign-in: you're Drew
    uv run python scripts/serve.py --no-login --as alice.demo@northeastern.edu

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

from backend.config import DATA_DIR, load_config
from backend.app_api import AppApi
from backend.graph.query_understanding import ClaudeQueryExtractor, QueryReader
from backend.server import ApiError, DiscoverApi, demo_graph_factory, serve
from backend.sessions import SessionStore

log = logging.getLogger("serve")
DEFAULT_DEV_ACCOUNT = "drew.demo@northeastern.edu"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-ai", action="store_true", help="keyword search only, no API calls")
    parser.add_argument("--no-login", action="store_true",
                        help="development: skip sign-in, everyone is signed in automatically")
    parser.add_argument("--as", dest="login_as", default=DEFAULT_DEV_ACCOUNT, metavar="EMAIL",
                        help=f"with --no-login: who you're signed in as (default {DEFAULT_DEV_ACCOUNT})")
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
    # Search reads sentences with one small Claude call; --no-ai reads them locally.
    reader = QueryReader(None if args.no_ai else ClaudeQueryExtractor())
    try:
        server = serve(DiscoverApi(graphs), args.port, app=AppApi(graphs, reader=reader),
                       sessions=SessionStore(audit_path=DATA_DIR / "view_as.log"),
                       auto_login_email=args.login_as if args.no_login else None)
    except ApiError as error:  # e.g. --as with a non-Northeastern email
        log.error("--as %s: %s", args.login_as, error.message)
        return 1
    log.info("Collectivize demo on http://127.0.0.1:%d/  (sign in with e.g. drew.demo@northeastern.edu;"
             " platform admin: pat.demo@northeastern.edu)", args.port)
    log.info("AI search: %s", "off (keywords only)" if args.no_ai else "on")
    if args.no_login:
        log.warning("LOGIN IS OFF (--no-login): everyone is signed in as %s. "
                    "Development only; drop the flag to turn login back on.", args.login_as)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
