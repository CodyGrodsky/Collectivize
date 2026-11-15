"""Set up (or change) an admin's username and password for /admin.

    uv run python scripts/set_admin_password.py                  # asks for everything
    uv run python scripts/set_admin_password.py --username kj    # asks for the password
    uv run python scripts/set_admin_password.py --generate       # makes a strong password for you
    uv run python scripts/set_admin_password.py --owner          # this login manages the other admins
    uv run python scripts/set_admin_password.py --make-owner kj  # make an existing login the owner

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

from dataclasses import replace

from backend.admin_auth import (
    CREDENTIALS_PATH,
    MAX_USERNAME,
    MIN_PASSWORD,
    AdminAuth,
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
    parser.add_argument("--owner", action="store_true",
                        help="this login is the owner: it manages the other admins and their powers")
    parser.add_argument("--make-owner", metavar="USERNAME",
                        help="make an existing login the owner (its password stays the same)")
    args = parser.parse_args(argv)
    if args.make_owner:
        return make_owner(args.make_owner)

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
        credential = AdminCredential(username, args.email.strip().lower(), hash_password(password),
                                     "owner" if args.owner else "admin")
        save_credential(credential)
    except AdminAuthError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"Saved admin '{username}' to {CREDENTIALS_PATH}.\n")
    if args.generate:
        sys.stdout.write(f"Password (shown once, save it in your password manager): {password}\n")
    sys.stdout.write("Restart the server, then sign in at http://127.0.0.1:8000/admin\n")
    return 0


def make_owner(username: str) -> int:
    try:
        credential = AdminAuth.load().credential(username)
        if credential is None:
            sys.stderr.write(f"There is no admin login called '{username}'.\n")
            return 1
        save_credential(replace(credential, role="owner"))
    except AdminAuthError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"'{credential.username}' is now the owner. Restart the server.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
