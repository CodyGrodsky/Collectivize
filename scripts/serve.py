"""Run the local demo server: the frontend pages plus the JSON API.

Usage:
    uv run python scripts/serve.py              # http://127.0.0.1:3000/
    uv run python scripts/serve.py --no-ai      # search by keywords, no API calls
    uv run python scripts/serve.py --port 8080
    uv run python scripts/serve.py --login      # ask for the .edu sign-in this run
    uv run python scripts/serve.py --no-login   # skip sign-in this run: you're Drew
    uv run python scripts/serve.py --as alice.demo@northeastern.edu   # skipped sign-in: be Alice

Whether sign-in is asked for by default is LOGIN_REQUIRED in
src/backend/config.py (on). Sign-ins are remembered in data/sessions.json
until the person signs out. Admins sign in on the same screen with their
admin username and password (scripts/set_admin_password.py), plus Duo when
it is set up (scripts/set_duo.py).

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

from backend.config import DATA_DIR, EMAIL_VERIFICATION, LOGIN_REQUIRED, load_config
from backend.accounts import AccountsApi
from backend.admin_auth import AdminAuth, AdminAuthError
from backend.app_api import AppApi
from backend.audit import AuditLog
from backend.duo import DuoError, DuoGate
from backend.email_verification import EmailVerifier
from backend.mailer import ConsoleMailer, MailError, load_mailer
from backend.graph.query_understanding import ClaudeQueryExtractor, QueryReader
from backend.server import ApiError, DiscoverApi, demo_graph_factory, serve
from backend.sessions import SessionStore

log = logging.getLogger("serve")
DEFAULT_DEV_ACCOUNT = "drew.demo@northeastern.edu"


def login_is_on(flag: bool | None, default: bool = LOGIN_REQUIRED) -> bool:
    """Whether to ask for sign-in: `--login` / `--no-login` if given,
    otherwise LOGIN_REQUIRED from config.py."""
    return default if flag is None else flag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--no-ai", action="store_true", help="keyword search only, no API calls")
    login = parser.add_mutually_exclusive_group()
    login.add_argument("--login", dest="login", action="store_true", default=None,
                       help="ask for the .edu sign-in, whatever LOGIN_REQUIRED says")
    login.add_argument("--no-login", dest="login", action="store_false",
                       help="development: skip sign-in, everyone is signed in automatically")
    parser.add_argument("--as", dest="login_as", default=DEFAULT_DEV_ACCOUNT, metavar="EMAIL",
                        help=f"with sign-in skipped: who you're signed in as (default {DEFAULT_DEV_ACCOUNT})")
    args = parser.parse_args(argv)
    login_on = login_is_on(args.login)
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
        admin_auth = AdminAuth.load()
        duo = DuoGate.load()
        mailer = load_mailer()
    except (AdminAuthError, DuoError, MailError) as error:
        log.error("%s", error)
        return 1
    sessions = SessionStore(audit_path=DATA_DIR / "view_as.log", path=DATA_DIR / "sessions.json")
    accounts = AccountsApi(graphs, sessions, audit=AuditLog(DATA_DIR / "admin_audit.log"),
                           owners=admin_auth.owner_emails, logins=admin_auth.emails)
    try:
        server = serve(DiscoverApi(graphs), args.port, app=AppApi(graphs, reader=reader),
                       sessions=sessions, accounts=accounts, admin_auth=admin_auth, duo=duo,
                       verifier=EmailVerifier(mailer) if EMAIL_VERIFICATION else None,
                       auto_login_email=None if login_on else args.login_as)
    except ApiError as error:  # e.g. --as with a non-Northeastern email, or an admin
        log.error("--as %s: %s", args.login_as, error.message)
        return 1
    log.info("Collectivize demo on http://127.0.0.1:%d/  (sign in with e.g. drew.demo@northeastern.edu)",
             args.port)
    if admin_auth.is_set_up:
        log.info("Admin sign-in: type your admin username on the sign-in screen (or open /admin)")
        if duo is not None:
            log.info("Admin sign-ins also need Duo approval.")
        elif admin_auth.without_second_step():
            log.warning("These admin logins are PASSWORD ONLY (no second step): %s. Add an "
                        "authenticator app to each: uv run python scripts/set_admin_password.py "
                        "--add-authenticator <username>", ", ".join(admin_auth.without_second_step()))
        else:
            log.info("Admin sign-ins also need an authenticator code.")
        for email, names in admin_auth.shared_accounts().items():
            log.warning("%d admin logins open the same account (%s): %s. The admin log names the "
                        "login, but each admin should have their own account: "
                        "set_admin_password.py --email <their own address>",
                        len(names), email, ", ".join(names))
        if not admin_auth.owner_emails:
            log.warning("No admin login is the owner, so nobody can manage admins or their powers. "
                        "Run: uv run python scripts/set_admin_password.py --make-owner <username>")
    else:
        log.warning("Admin sign-in isn't set up, so nobody can open /admin. "
                    "Run: uv run python scripts/set_admin_password.py")
    if not EMAIL_VERIFICATION:
        log.warning("Email verification is OFF (EMAIL_VERIFICATION in config.py): "
                    "typing an address is enough to sign in.")
    elif isinstance(mailer, ConsoleMailer):
        log.warning("Mail isn't set up, so sign-in codes are shown HERE in this terminal, "
                    "not emailed. To email them run: uv run python scripts/set_mail.py")
    else:
        log.info("Sign-in codes are emailed (demo accounts' codes are shown here).")
    log.info("AI search: %s", "off (keywords only)" if args.no_ai else "on")
    if not login_on:
        log.warning("SIGN-IN IS OFF: everyone is signed in as %s. Development only. To turn it "
                    "back on, set LOGIN_REQUIRED = True in src/backend/config.py (or run with "
                    "--login).", args.login_as)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
