"""The admin sign-in: hashed credentials in a local file, a hidden /admin
entrance, lock-out after wrong passwords, and no email sign-in for admins."""

import json
import threading
from contextlib import contextmanager

import pytest

from backend.admin_auth import (
    LOCK_SECONDS,
    MAX_FAILURES,
    AdminAuth,
    AdminAuthError,
    AdminCredential,
    LockedOutError,
    check_password,
    hash_password,
    save_credential,
)
from backend.app_api import AppApi
from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data
from backend.server import ApiError, DiscoverApi, serve
from backend.sessions import SessionStore

from .conftest import NOW
from .test_access import call, sign_in

PAT = "pat.demo@northeastern.edu"
PASSWORD = "correct horse battery"
HASH = hash_password(PASSWORD)


def auth(clock=None, email=PAT):
    kwargs = {"clock": clock} if clock else {}
    return AdminAuth((AdminCredential("Boss", email, HASH),), **kwargs)


@contextmanager
def running(admin_auth, **kwargs):
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=SessionStore(), admin_auth=admin_auth, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", result, srv
    finally:
        srv.shutdown()
        srv.server_close()


def admin_sign_in(base, username, password):
    status, headers, body = call(f"{base}/api/admin/signin", {"username": username, "password": password})
    token = headers.get("Set-Cookie", "").split(";", 1)[0].partition("=")[2]
    return status, json.loads(body), token


# --- credentials -----------------------------------------------------------------------


def test_a_password_is_stored_only_as_a_salted_hash():
    assert PASSWORD not in HASH and hash_password(PASSWORD) != HASH  # a new salt each time
    assert check_password(PASSWORD, HASH)
    assert not check_password(PASSWORD + "x", HASH) and not check_password(PASSWORD, "garbage")


def test_a_short_password_is_refused():
    with pytest.raises(AdminAuthError):
        hash_password("short")


def test_the_credentials_file_round_trips_and_is_private(tmp_path):
    path = tmp_path / "config" / "admin.local.json"
    assert not AdminAuth.load(path).is_set_up  # no file yet: nobody can sign in
    save_credential(AdminCredential("Boss", PAT, HASH), path)
    save_credential(AdminCredential("boss", PAT, HASH), path)  # same name: replaced
    assert PASSWORD not in path.read_text() and len(json.loads(path.read_text())["admins"]) == 1
    assert path.stat().st_mode & 0o077 == 0
    assert AdminAuth.load(path).verify("BOSS ", PASSWORD) == PAT


@pytest.mark.parametrize("text", [
    '{"admins": [{"username": "a", "email": "a@b.c", "password": "plain text here"}]}',
    '{"admins": [{"username": "a"}]}', "not json", '{"nope": []}',
])
def test_a_plain_password_or_broken_file_is_refused(tmp_path, text):
    path = tmp_path / "admin.local.json"
    path.write_text(text)
    with pytest.raises(AdminAuthError):
        AdminAuth.load(path)


def test_wrong_passwords_lock_the_username_for_a_while():
    now = [0.0]
    admins = auth(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        assert admins.verify("boss", "wrong password!") is None
    with pytest.raises(LockedOutError):
        admins.verify("boss", PASSWORD)  # even the right one, while locked
    assert admins.verify("nobody", "wrong password!") is None  # unknown names never lock
    now[0] = LOCK_SECONDS + 1
    assert admins.verify("boss", PASSWORD) == PAT


# --- over HTTP -------------------------------------------------------------------------


def test_admin_signs_in_with_username_and_password_only():
    with running(auth()) as (base, result, srv):
        status, data, _ = admin_sign_in(base, "boss", "wrong password!")
        assert status == 401 and data == {"error": "wrong username or password"}
        assert admin_sign_in(base, "nobody", PASSWORD)[:2] == (401, data)  # same answer
        for bad in ({"username": "boss"}, {"username": 5, "password": PASSWORD},
                    {"username": "boss", "password": "x" * 500}):
            assert call(f"{base}/api/admin/signin", bad)[0] == 401
        status, me, token = admin_sign_in(base, "boss", PASSWORD)
        assert status == 200 and me["is_platform_admin"] and me["id"] == result.people["pat"]
        assert call(f"{base}/api/admin/users", token=token)[0] == 200
        actions = [e["action"] for e in srv.accounts.audit.entries()]
        assert actions == ["sign-in failed", "sign-in failed", "sign-in"]
        assert PASSWORD not in json.dumps(srv.accounts.audit.entries())


def test_an_admin_account_cannot_sign_in_by_email_once_admin_sign_in_exists():
    with running(auth()) as (base, _, _):
        status, headers, _ = call(f"{base}/api/signin", {"email": PAT})
        assert status == 403 and "Set-Cookie" not in headers
        sign_in(base, "drew.demo@northeastern.edu")  # everyone else is unaffected
    with running(AdminAuth()) as (base, _, _):  # not set up yet: still closed, no way in
        assert call(f"{base}/api/signin", {"email": PAT})[0] == 403
        assert admin_sign_in(base, "boss", PASSWORD)[0] == 401


def test_the_admin_entrance_shows_the_sign_in_until_you_are_an_admin():
    with running(auth()) as (base, _, _):
        student, _ = sign_in(base, "drew.demo@northeastern.edu")
        for token in (None, student):
            status, headers, _ = call(f"{base}/admin", token=token)
            assert status == 302 and headers["Location"].endswith("admin-login.html")
        assert call(f"{base}/src/frontend/admin-login.html")[0] == 200  # public page
        _, _, admin = admin_sign_in(base, "boss", PASSWORD)
        assert call(f"{base}/admin", token=admin)[1]["Location"].endswith("admin.html")


def test_the_password_only_opens_a_real_platform_admin_account():
    with running(auth(email="drew.demo@northeastern.edu")) as (base, _, _):
        assert admin_sign_in(base, "boss", PASSWORD)[0] == 401  # Drew isn't an admin


def test_too_many_wrong_passwords_over_http_is_429():
    with running(auth()) as (base, _, _):
        for _ in range(MAX_FAILURES):
            admin_sign_in(base, "boss", "wrong password!")
        assert admin_sign_in(base, "boss", PASSWORD)[0] == 429


def test_auto_login_refuses_an_admin_account():
    with pytest.raises(ApiError), running(auth(), auto_login_email=PAT):
        pass


def test_without_admin_auth_the_endpoint_does_not_exist():
    with running(None) as (base, _, _):
        assert admin_sign_in(base, "boss", PASSWORD)[0] == 404
