"""Set up (or change) an admin's username and password for /admin.

    uv run python scripts/set_admin_password.py                  # asks for everything
    uv run python scripts/set_admin_password.py --username kj    # asks for the password
    uv run python scripts/set_admin_password.py --generate       # makes a strong password for you

Writes config/admin.local.json (git-ignored, readable only by you). Only a
salted hash of the password is stored. `--email` is the platform-admin
account the sign-in opens (default: the demo admin, Pat).
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.admin_auth import (
    CREDENTIALS_PATH,
    MAX_USERNAME,
    MIN_PASSWORD,
    AdminAuthError,
    AdminCredential,
    hash_password,
    save_credential,
)

DEFAULT_EMAIL = "pat.demo@northeastern.edu"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username")
    parser.add_argument("--email", default=DEFAULT_EMAIL,
                        help=f"the platform-admin account to sign in to (default {DEFAULT_EMAIL})")
    parser.add_argument("--generate", action="store_true", help="make a strong password and show it once")
    args = parser.parse_args(argv)

    username = (args.username or input("Admin username: ")).strip()
    if not 0 < len(username) <= MAX_USERNAME:
        sys.stderr.write(f"The username must be 1 to {MAX_USERNAME} characters.\n")
        return 1
    if args.generate:
        password = secrets.token_urlsafe(18)
    else:
        password = getpass.getpass(f"Password (at least {MIN_PASSWORD} characters): ")
        if password != getpass.getpass("Again: "):
            sys.stderr.write("Those didn't match. Nothing was saved.\n")
            return 1
    try:
        credential = AdminCredential(username, args.email.strip().lower(), hash_password(password))
        save_credential(credential)
    except AdminAuthError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"Saved admin '{username}' to {CREDENTIALS_PATH}.\n")
    if args.generate:
        sys.stdout.write(f"Password (shown once, save it in your password manager): {password}\n")
    sys.stdout.write("Restart the server, then sign in at http://127.0.0.1:8000/admin\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
