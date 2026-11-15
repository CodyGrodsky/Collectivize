"""Vercel entry point: the whole demo (pages + JSON API) as one serverless function.

Branch `vercel-demo` only. See VERCEL.md.

Every request is routed here (vercel.json). The real request handler from
backend/server.py answers it, against a copy of the bundled fictional demo
database (demo/collectivize-demo.db) in /tmp. Each function instance gets its
own copy, so changes (RSVPs, profile edits) last only while that instance is
warm, then everything resets to the demo data. That is on purpose.

Public-demo settings: everyone is signed in automatically as the demo account,
no email codes, AI search off (no Anthropic calls), no admin logins.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BUNDLED_DB = ROOT / "demo" / "collectivize-demo.db"
LIVE_DB = Path(os.environ.get("TMPDIR", "/tmp")) / "collectivize" / "demo.db"
DEMO_ACCOUNT = "drew.demo@northeastern.edu"

if not LIVE_DB.exists():
    LIVE_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_DB, LIVE_DB)
os.environ["COLLECTIVIZE_ENV"] = "test"
os.environ["COLLECTIVIZE_DB_PATH"] = str(LIVE_DB)

from backend.accounts import AccountsApi  # noqa: E402
from backend.admin_auth import AdminAuth  # noqa: E402
from backend.app_api import AppApi  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.graph.query_understanding import QueryReader  # noqa: E402
from backend.server import DemoServer, DiscoverApi, RequestHandler, demo_graph_factory  # noqa: E402
from backend.sessions import SessionStore  # noqa: E402


class _NoSocketServer(DemoServer):
    """The demo server's state (graphs, sessions, settings) without opening a
    port: Vercel's runtime owns the socket and hands each request to `handler`."""

    def server_bind(self) -> None:
        pass

    def server_activate(self) -> None:
        pass


_graphs = demo_graph_factory(load_config(), use_ai=False)
_sessions = SessionStore()  # in memory: an instance's sign-ins end with it
DEMO = _NoSocketServer(
    ("127.0.0.1", 0),
    DiscoverApi(_graphs),
    app=AppApi(_graphs, reader=QueryReader(None)),
    sessions=_sessions,
    accounts=AccountsApi(_graphs, _sessions),
    admin_auth=AdminAuth(),  # nobody: /admin stays locked on the public demo
    verifier=None,  # no email codes
    auto_login_email=DEMO_ACCOUNT,
)


class handler(RequestHandler):  # noqa: N801 (Vercel looks for this name)
    def __init__(self, request, client_address, server) -> None:  # type: ignore[no-untyped-def]
        super().__init__(request, client_address, DEMO)

    def parse_request(self) -> bool:
        """vercel.json rewrites every address to /api/index?__path=<original>;
        put the original address back before the app routes it."""
        ok = super().parse_request()
        if ok:
            url = urlsplit(self.path)
            params = parse_qsl(url.query, keep_blank_values=True)
            original = [v for k, v in params if k == "__path"]
            if original:
                rest = urlencode([(k, v) for k, v in params if k != "__path"])
                self.path = "/" + original[0].lstrip("/") + ("?" + rest if rest else "")
        return ok
