"""Who did what (the admin log names the login), staff accounts students
can't find, an authenticator app as the admin's second step, and changing
a sign-in email only with a code sent to the new address."""

import base64
import json
import threading
from contextlib import contextmanager

import pytest

from backend.accounts import AccountsApi
from backend.admin_auth import (
    MAX_FAILURES,
    AdminAuth,
    AdminAuthError,
    AdminCredential,
    hash_password,
    save_credential,
)
from backend.admin_setup import ensure_admin_account, own_account_email
from backend.app_api import AppApi
from backend.config import Environment
from backend.email_verification import EmailVerifier
from backend.graph import CollectivizeGraph
from backend.objects import Person
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.sessions import SessionStore
from backend.totp import code_at, matching_step, new_secret, setup_uri

from .conftest import NOW
from .test_access import call

PAT, DREW, CASEY = (f"{n}.demo@northeastern.edu" for n in ("pat", "drew", "casey"))
NEW = "drew.new@northeastern.edu"
PASSWORD = "correct horse battery"
SECRET = new_secret()
T0 = 1_700_000_000.0


class Outbox:
    def __init__(self):
        self.codes, self.notices = {}, []

    def send_code(self, to, code, minutes):
        self.codes[to] = code

    def send_notice(self, to, subject, text):
        self.notices.append((to, subject))


@contextmanager
def running(credentials, *, outbox=None, wall=None, owners=None):
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    auth = AdminAuth(tuple(credentials), **({"wall_clock": wall} if wall else {}))
    sessions = SessionStore()
    accounts = AccountsApi(factory, sessions, clock=lambda: NOW,
                           owners=auth.owner_emails if owners is None else owners)
    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=sessions, admin_auth=auth, accounts=accounts,
                verifier=EmailVerifier(outbox) if outbox else None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", graph, result, srv
    finally:
        srv.shutdown()
        srv.server_close()


def post(base, path, body, token=None):
    status, headers, raw = call(f"{base}{path}", body, token=token)
    return status, json.loads(raw), headers.get("Set-Cookie", "").split(";")[0].partition("=")[2]


def get(base, path, token):
    status, _, raw = call(f"{base}{path}", token=token)
    return status, json.loads(raw)


def login(name, **extra):
    return AdminCredential(name, PAT, hash_password(PASSWORD), **extra)


def student(base, outbox, email=DREW):
    post(base, "/api/signin", {"email": email})
    return post(base, "/api/signin/verify", {"email": email, "code": outbox.codes[email]})[2]


# --- 9. who did what -------------------------------------------------------------------


def test_the_admin_log_names_the_login_even_when_logins_share_an_account():
    with running([login("kj", role="owner"), login("billy")]) as (base, _, result, srv):
        drew = result.people["drew"]
        kj = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})[2]
        billy = post(base, "/api/admin/signin", {"username": "Billy", "password": PASSWORD})[2]
        post(base, "/api/admin/signin", {"username": "billy", "password": "wrong password!"})
        assert post(base, "/api/admin/account", {"person_id": drew, "bio": "one"}, kj)[0] == 200
        assert post(base, "/api/admin/account", {"person_id": drew, "bio": "two"}, billy)[0] == 200
        assert post(base, "/api/admin/view-as", {"person_id": drew}, kj)[0] == 200
        entries = srv.accounts.audit.entries()
        assert [(e["action"], e["login"]) for e in entries] == [
            ("sign-in", "kj"), ("sign-in", "billy"), ("sign-in failed", "billy"),
            ("update", "kj"), ("update", "billy"),
        ]
        assert len({e["admin"] for e in entries if e["action"] == "update"}) == 1  # one account, two people
        shown = get(base, "/api/admin/audit", kj)[1]["entries"]
        assert shown[0]["action"] == "view-as start" and {"kj", "billy"} <= {e.get("login") for e in shown}


def test_a_student_action_is_never_logged_under_an_admin_login():
    outbox = Outbox()
    with running([login("kj", role="owner")], outbox=outbox) as (base, _, _, srv):
        post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})
        token = student(base, outbox)
        assert post(base, "/api/account/delete", {"confirm": "nope"}, token)[0] == 400
        assert srv.sessions.get(token).login == ""


def test_each_new_login_needs_its_own_account():
    auth = AdminAuth((login("kj"),))
    with pytest.raises(AdminAuthError) as shared:
        own_account_email(auth, "billy", PAT)
    assert "'kj' already opens" in str(shared.value)
    assert own_account_email(auth, "KJ", " Pat.Demo+x@northeastern.edu") == PAT  # the same login again
    assert own_account_email(auth, "billy", "Billy.J@northeastern.edu") == "billy.j@northeastern.edu"
    with pytest.raises(AdminAuthError):
        own_account_email(auth, "billy", "billy@gmail.com")
    assert AdminAuth((login("a"), login("b"))).shared_accounts() == {PAT: ["a", "b"]}


def test_setting_up_a_login_creates_or_promotes_its_account():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    person, done = ensure_admin_account(graph, "billy.j@northeastern.edu", NOW)
    assert done == "created" and person.is_platform_admin and person.admin_powers == ()
    person, done = ensure_admin_account(graph, DREW, NOW)
    assert done == "promoted" and person.id == result.people["drew"] and person.is_platform_admin
    assert ensure_admin_account(graph, PAT, NOW)[1] == "ready"


def test_the_owner_can_hide_a_staff_account_from_students():
    outbox = Outbox()
    with running([login("kj", role="owner")], outbox=outbox) as (base, _, result, srv):
        pat = result.people["pat"]
        owner = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})[2]
        drew = student(base, outbox)
        assert get(base, "/api/find?q=pat&types=people", drew)[1]["total"] == 1
        status, data, _ = post(base, "/api/admin/admins", {"person_id": pat, "hidden": True}, owner)
        assert status == 200 and data["hidden"] is True  # the owner's own account can be hidden too
        assert get(base, "/api/find?q=pat&types=people", drew)[1]["total"] == 0
        assert get(base, f"/api/network?center={pat}", drew)[0] == 404  # looks like it isn't there
        assert get(base, "/api/me", owner)[0] == 200 and get(base, "/api/admin/users?q=pat", owner)[1]["total"] == 1
        assert srv.accounts.audit.entries()[-1]["action"] == "hide admin"
        assert post(base, "/api/admin/admins", {"person_id": pat, "hidden": False}, owner)[1]["hidden"] is False
        assert get(base, "/api/find?q=pat&types=people", drew)[1]["total"] == 1
        # Only admin accounts, only by the owner, only true or false.
        assert post(base, "/api/admin/admins", {"person_id": result.people["drew"], "hidden": True}, owner)[0] == 400
        assert post(base, "/api/admin/admins", {"person_id": pat, "hidden": "yes"}, owner)[0] == 400
        assert post(base, "/api/admin/admins", {"person_id": pat, "hidden": True}, drew)[0] == 403


def test_someone_who_stops_being_an_admin_is_findable_again():
    with running([login("kj", role="owner")]) as (base, graph, result, _):
        casey = result.people["casey"]
        owner = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})[2]
        post(base, "/api/admin/admins", {"person_id": casey, "is_admin": True, "hidden": True}, owner)
        assert graph.store.node(casey, Person).visibility.name == "PRIVATE"
        post(base, "/api/admin/admins", {"person_id": casey, "is_admin": False}, owner)
        assert graph.store.node(casey, Person).visibility.name == "NETWORK"


# --- 10. the authenticator step --------------------------------------------------------


def test_codes_match_the_standard_and_only_the_current_ones():
    key = base64.b32encode(b"12345678901234567890").decode()  # RFC 6238's test key
    assert code_at(key, 59 // 30) == "287082" and code_at(key, 1111111109 // 30) == "081804"
    now = 1_700_000_000
    step = now // 30
    assert matching_step(key, code_at(key, step), now) == step
    assert matching_step(key, f" {code_at(key, step - 1)} ", now) == step - 1  # a little clock drift
    assert matching_step(key, code_at(key, step + 5), now) is None  # too far off
    for junk in ("", "12345", "1234567", "abcdef", "١٢٣٤٥٦"):
        assert matching_step(key, junk, now) is None
    assert matching_step("not base32!!", "123456", now) is None
    assert len(new_secret()) == 32 and new_secret() != new_secret()
    assert setup_uri(key, "kj").startswith("otpauth://totp/Collectivize%3Akj?secret=")


def test_an_admin_with_an_authenticator_needs_its_code_after_the_password():
    now = [T0]
    code = lambda offset=0: code_at(SECRET, int(now[0] // 30) + offset)
    with running([login("kj", totp_secret=SECRET)], wall=lambda: now[0]) as (base, _, _, srv):
        status, data, cookie = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})
        assert status == 401 and data["needs_passcode"] and not cookie  # the password alone isn't enough
        wrong = "000000" if code() != "000000" else "111111"
        status, data, cookie = post(base, "/api/admin/signin",
                                    {"username": "kj", "password": PASSWORD, "passcode": wrong})
        assert status == 401 and data["needs_passcode"] and not cookie
        assert post(base, "/api/admin/signin", {"username": "kj", "password": "wrong password!",
                                               "passcode": code()})[0] == 401  # the code alone isn't either
        status, me, cookie = post(base, "/api/admin/signin",
                                  {"username": "kj", "password": PASSWORD, "passcode": code()})
        assert status == 200 and me["is_platform_admin"] and cookie
        assert srv.accounts.audit.entries()[-1]["detail"] == "password + authenticator"
        # The same code can't be used a second time.
        assert post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD, "passcode": code()})[0] == 401
        now[0] += 30
        assert post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD, "passcode": code()})[0] == 200


def test_wrong_codes_lock_the_login_like_wrong_passwords():
    now = [T0]
    with running([login("kj", totp_secret=SECRET)], wall=lambda: now[0]) as (base, _, _, _):
        good = code_at(SECRET, int(now[0] // 30))
        wrong = "000000" if good != "000000" else "111111"
        for _ in range(MAX_FAILURES):
            post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD, "passcode": wrong})
        assert post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD, "passcode": good})[0] == 429


def test_a_login_without_an_authenticator_still_signs_in_and_is_reported(tmp_path):
    auth = AdminAuth((login("kj", totp_secret=SECRET), login("billy")))
    assert auth.without_second_step() == ["billy"]
    with running([login("billy")]) as (base, _, _, srv):
        assert post(base, "/api/admin/signin", {"username": "billy", "password": PASSWORD})[0] == 200
        assert srv.accounts.audit.entries()[-1]["detail"] == "password"
    path = tmp_path / "admin.local.json"
    save_credential(login("kj", totp_secret=SECRET, role="owner"), path)
    assert AdminAuth.load(path).credential("kj").totp_secret == SECRET  # survives a restart


# --- 11. changing a sign-in email ------------------------------------------------------


def test_changing_my_email_needs_a_code_from_the_new_address():
    outbox = Outbox()
    with running([login("kj")], outbox=outbox) as (base, graph, result, srv):
        here, elsewhere = student(base, outbox), student(base, outbox)
        status, data, _ = post(base, "/api/account/email", {"email": " Drew.New+x@northeastern.edu"}, here)
        assert status == 200 and data == {"verify": True, "email": NEW, "emailed": True}
        assert graph.store.node(result.people["drew"], Person).email == DREW  # nothing changed yet
        assert post(base, "/api/account/email/verify", {"email": NEW, "code": "000000"}, here)[0] == 400
        assert get(base, "/api/me", here)[0] == 200  # a wrong code doesn't sign you out
        status, account, _ = post(base, "/api/account/email/verify", {"email": NEW, "code": outbox.codes[NEW]}, here)
        assert status == 200 and account["email"] == NEW
        assert graph.store.node(result.people["drew"], Person).email == NEW
        assert get(base, "/api/me", here)[0] == 200 and get(base, "/api/me", elsewhere)[0] == 401
        assert outbox.notices == [(DREW, "Your Collectivize sign-in email was changed")]  # the old address is told
        # The old address no longer leads to this account; the new one does.
        assert srv.app.account_id(NEW) == result.people["drew"] and srv.app.account_id(DREW) is None


def test_an_address_someone_else_uses_gets_the_same_answer_and_no_code():
    outbox = Outbox()
    with running([login("kj")], outbox=outbox) as (base, graph, result, _):
        token = student(base, outbox)
        status, data, _ = post(base, "/api/account/email", {"email": CASEY}, token)
        assert status == 200 and data["verify"] is True and CASEY not in outbox.codes
        assert post(base, "/api/account/email/verify", {"email": CASEY, "code": "123456"}, token)[0] == 400
        assert graph.store.node(result.people["drew"], Person).email == DREW
        for bad in ({"email": "x@gmail.com"}, {"email": DREW}, {"email": 5}, {}):
            assert post(base, "/api/account/email", bad, token)[0] == 400, bad


def test_a_sign_in_code_cannot_confirm_an_email_change():
    outbox = Outbox()
    with running([login("kj")], outbox=outbox) as (base, graph, result, _):
        token = student(base, outbox)
        post(base, "/api/signin", {"email": NEW})  # anyone can ask for a sign-in code for NEW
        status, _, _ = post(base, "/api/account/email/verify", {"email": NEW, "code": outbox.codes[NEW]}, token)
        assert status == 400 and graph.store.node(result.people["drew"], Person).email == DREW


def test_admin_emails_and_servers_without_mail_cannot_change_this_way():
    outbox = Outbox()
    with running([login("kj")], outbox=outbox) as (base, _, _, _):
        admin = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})[2]
        assert post(base, "/api/account/email", {"email": NEW}, admin)[0] == 403
        assert post(base, "/api/account/email", {"email": NEW})[0] == 401  # signed out
    with running([login("kj")]) as (base, _, _, srv):  # no verifier
        token = srv.sessions.create(srv.app.account_id(DREW))
        assert post(base, "/api/account/email", {"email": NEW}, token)[0] == 503


def test_when_the_owner_changes_an_email_the_account_is_signed_out_and_the_old_address_told():
    outbox = Outbox()
    with running([login("kj", role="owner")], outbox=outbox) as (base, _, result, _):
        owner = post(base, "/api/admin/signin", {"username": "kj", "password": PASSWORD})[2]
        token = student(base, outbox)
        status, account, _ = post(base, "/api/admin/account", {"person_id": result.people["drew"], "email": NEW}, owner)
        assert status == 200 and account["email"] == NEW
        assert get(base, "/api/me", token)[0] == 401  # signed out everywhere
        assert outbox.notices == [(DREW, "Your Collectivize sign-in email was changed")]
        post(base, "/api/admin/account", {"person_id": result.people["drew"], "bio": "hi"}, owner)
        assert len(outbox.notices) == 1  # only an email change sends one
