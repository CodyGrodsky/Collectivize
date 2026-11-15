"""Set up (or change) an admin's sign-in.

    uv run python scripts/set_admin_password.py --email you@northeastern.edu   # a new admin login
    uv run python scripts/set_admin_password.py --username kj                  # change kj's password
    uv run python scripts/set_admin_password.py --generate ...                 # make a strong password
    uv run python scripts/set_admin_password.py --owner ...                    # this login manages the other admins
    uv run python scripts/set_admin_password.py --make-owner kj                # make an existing login the owner
    uv run python scripts/set_admin_password.py --add-authenticator kj         # second step: an authenticator app
    uv run python scripts/set_admin_password.py --remove-authenticator kj

`--email` is the admin's own Northeastern account: the login opens that
account, and it is created or made a platform admin if needed. Each login
has its own account, so the admin log shows who did what.

The authenticator step needs no outside account: any authenticator app
works (Duo Mobile, Google Authenticator, 1Password). After it is added,
signing in needs the password and the app's current six-digit code.

Writes config/admin.local.json (git-ignored, readable only by you). Only a
salted hash of the password is stored.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
from backend.admin_setup import ensure_admin_account, own_account_email
from backend.config import load_config
from backend.database import open_graph
from backend.objects.base import utc_now
from backend.totp import matching_step, new_secret, setup_uri


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username")
    parser.add_argument("--email", help="the admin's own Northeastern account, which this login opens")
    parser.add_argument("--generate", action="store_true", help="make a strong password and show it once")
    parser.add_argument("--owner", action="store_true",
                        help="this login is the owner: it manages the other admins and their powers")
    parser.add_argument("--duo-username", default="",
                        help="their username in Duo, if it differs from the admin username")
    parser.add_argument("--make-owner", metavar="USERNAME",
                        help="make an existing login the owner (its password stays the same)")
    parser.add_argument("--add-authenticator", metavar="USERNAME",
                        help="add an authenticator app as this login's second step")
    parser.add_argument("--remove-authenticator", metavar="USERNAME",
                        help="remove this login's authenticator step")
    args = parser.parse_args(argv)
    try:
        if args.make_owner:
            return change(args.make_owner, "is now the owner", role="owner")
        if args.remove_authenticator:
            return change(args.remove_authenticator, "no longer needs an authenticator code",
                          totp_secret="")
        if args.add_authenticator:
            return add_authenticator(args.add_authenticator)
        return set_login(args)
    except AdminAuthError as error:
        sys.stderr.write(f"{error}\n")
        return 1


def set_login(args: argparse.Namespace) -> int:
    username = (args.username or input("Admin username: ")).strip()
    if not 0 < len(username) <= MAX_USERNAME:
        sys.stderr.write(f"The username must be 1 to {MAX_USERNAME} characters.\n")
        return 1
    auth = AdminAuth.load()
    existing = auth.credential(username)
    email = args.email or (existing.email if existing else input(
        "Their own Northeastern email (the account this login opens): "))
    if existing and email.strip().lower() == existing.email:
        address = existing.email  # an existing login keeps its account
    else:
        address = own_account_email(auth, username, email)
    if args.generate:
        password = secrets.token_urlsafe(18)
    else:
        password = getpass.getpass(f"Password (at least {MIN_PASSWORD} characters): ")
        if password != getpass.getpass("Again: "):
            sys.stderr.write("Those didn't match. Nothing was saved.\n")
            return 1
    role = "owner" if args.owner or (existing and existing.role == "owner") else "admin"
    credential = AdminCredential(
        username, address, hash_password(password), role,
        args.duo_username.strip() or (existing.duo_username if existing else ""),
        existing.totp_secret if existing else "",  # a new password keeps the authenticator
    )
    with open_graph(load_config()) as graph:
        person, done = ensure_admin_account(graph, address, utc_now())
    save_credential(credential)
    account = {"created": "A new admin account was created for", "promoted": "Made a platform admin of",
               "ready": "It opens the admin account"}[done]
    sys.stdout.write(f"Saved admin '{username}' to {CREDENTIALS_PATH}.\n{account} {address} "
                     f"({person.display_name}).\n")
    if args.generate:
        sys.stdout.write(f"Password (shown once, save it in your password manager): {password}\n")
    if not credential.totp_secret:
        sys.stdout.write("Next, add the second step: uv run python scripts/set_admin_password.py "
                         f"--add-authenticator '{username}'\n")
    sys.stdout.write("Restart the server, then type the username on the sign-in screen.\n")
    return 0


def change(username: str, done: str, **fields: str) -> int:
    credential = AdminAuth.load().credential(username)
    if credential is None:
        sys.stderr.write(f"There is no admin login called '{username}'.\n")
        return 1
    save_credential(replace(credential, **fields))
    sys.stdout.write(f"'{credential.username}' {done}. Restart the server.\n")
    return 0


def add_authenticator(username: str) -> int:
    credential = AdminAuth.load().credential(username)
    if credential is None:
        sys.stderr.write(f"There is no admin login called '{username}'.\n")
        return 1
    secret = new_secret()
    spaced = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    sys.stdout.write(
        "In your authenticator app (Duo Mobile, Google Authenticator, 1Password...) add an "
        "account and choose to enter a key by hand:\n\n"
        f"    Account name:  Collectivize ({credential.username})\n"
        f"    Key:           {spaced}\n"
        "    Type:          time-based, 6 digits\n\n"
        f"Or paste this link into an app that takes one:\n    {setup_uri(secret, credential.username)}\n\n"
    )
    code = input("Now type the 6-digit code the app shows, to check it works: ")
    if matching_step(secret, code) is None:
        sys.stderr.write("That code doesn't match. Nothing was saved; run this again.\n")
        return 1
    save_credential(replace(credential, totp_secret=secret))
    sys.stdout.write(f"Saved. '{credential.username}' now signs in with the password and the "
                     "app's code. Restart the server.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
