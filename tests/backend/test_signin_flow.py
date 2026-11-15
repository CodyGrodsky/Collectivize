"""One sign-in screen, remembered sign-ins, and Duo for admins only."""

import json
import threading
from contextlib import contextmanager

import pytest

from backend import config
from backend.admin_auth import AdminAuth, AdminCredential, hash_password
from backend.app_api import AppApi
from backend.config import Environment
from backend.duo import DuoError, DuoGate, DuoSettings, save_settings
from backend.graph import CollectivizeGraph
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.server_auth import ADMIN_SESSION_SECONDS, REMEMBER_SECONDS
from backend.sessions import SessionStore

from .conftest import NOW
from .test_access import call, sign_in

PAT = "pat.demo@northeastern.edu"
DREW = "drew.demo@northeastern.edu"
PASSWORD = "correct horse battery"
SETTINGS = DuoSettings("ikey", "skey", "api-test.duosecurity.com")
PUSH_PHONE = [{"device": "phone1", "capabilities": ["auto", "push", "mobile_otp"]}]


class FakeDuo:
    """Stands in for duo_client.Auth and records what it was asked."""

    def __init__(self, preauth="auth", auth="allow", devices=PUSH_PHONE, error=None):
        self._preauth, self._auth, self._devices, self._error = preauth, auth, devices, error
        self.calls = []

    def check(self):
        return {"time": 1}

    def preauth(self, username):
        self.calls.append(("preauth", username))
        if self._error:
            raise self._error
        return {"result": self._preauth, "devices": self._devices}

    def auth(self, factor, username, **options):
        self.calls.append((factor, username, options.get("passcode")))
        return {"result": self._auth, "status_msg": "x"}


def gate(fake):
    return DuoGate(SETTINGS, client_factory=lambda settings: fake)


def seeded():
    graph = CollectivizeGraph()
    return graph, seed_mock_data(graph, Environment.TEST, NOW)


@contextmanager
def running(*, duo=None, sessions=None, credential=None, world=None):
    graph, result = world or seeded()

    @contextmanager
    def factory():
        yield graph

    credential = credential or AdminCredential("Boss", PAT, hash_password(PASSWORD))
    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=sessions or SessionStore(), admin_auth=AdminAuth((credential,)), duo=duo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", result, srv
    finally:
        srv.shutdown()
        srv.server_close()


def admin_post(base, **body):
    status, headers, raw = call(f"{base}/api/admin/signin", body)
    return status, json.loads(raw), headers.get("Set-Cookie", "")


# --- one sign-in screen ------------------------------------------------------------------


def test_sign_in_is_required_by_default_and_the_site_opens_on_the_sign_in_screen():
    assert config.LOGIN_REQUIRED is True
    with running() as (base, _, _):
        status, _, body = call(f"{base}/")
        assert status == 200 and b'id="signin"' in body  # the sign-in screen itself
        status, headers, _ = call(f"{base}/discover")
        assert status == 302 and headers["Location"] == "/?next=%2Fdiscover"
        token, _ = sign_in(base, DREW)
        assert call(f"{base}/", token=token)[1]["Location"] == "/discover"


@pytest.mark.parametrize("typed", ["boss", " BOSS ", PAT, "Pat.Demo@northeastern.edu"])
def test_an_admin_name_or_email_typed_on_the_sign_in_screen_asks_for_the_password(typed):
    with running() as (base, result, _):
        status, headers, body = call(f"{base}/api/signin", {"email": typed})
        assert status == 200 and json.loads(body) == {"admin": True} and "Set-Cookie" not in headers
        status, me, cookie = admin_post(base, username=typed, password=PASSWORD)
        assert status == 200 and me["id"] == result.people["pat"] and me["is_platform_admin"]
        assert f"Max-Age={ADMIN_SESSION_SECONDS}" in cookie


def test_an_admin_username_that_looks_like_a_student_email_is_still_the_admin():
    looks_like_email = AdminCredential("kj@northeastern.edu", PAT, hash_password(PASSWORD))
    with running(credential=looks_like_email) as (base, result, _):
        _, headers, body = call(f"{base}/api/signin", {"email": "kj@northeastern.edu"})
        assert json.loads(body) == {"admin": True} and "Set-Cookie" not in headers  # no new account
        assert admin_post(base, username="kj@northeastern.edu", password=PASSWORD)[1]["id"] == result.people["pat"]


# --- remembered ------------------------------------------------------------------------


def test_a_sign_in_is_remembered_by_the_browser_for_thirty_days():
    with running() as (base, _, _):
        _, headers, _ = call(f"{base}/api/signin", {"email": DREW})
        assert f"Max-Age={REMEMBER_SECONDS}" in headers["Set-Cookie"] and REMEMBER_SECONDS == 30 * 86400


def test_a_sign_in_survives_a_server_restart_until_sign_out(tmp_path):
    path, world = tmp_path / "sessions.json", seeded()  # the same data before and after
    with running(sessions=SessionStore(path=path), world=world) as (base, result, _):
        drew, _ = sign_in(base, DREW)
        alice, _ = sign_in(base, "alice.demo@northeastern.edu")
        assert drew not in path.read_text() and result.people["drew"] in path.read_text()  # hashes only
        assert path.stat().st_mode & 0o077 == 0
        assert call(f"{base}/api/signout", {}, token=alice)[0] == 200
    with running(sessions=SessionStore(path=path), world=world) as (base, result, _):  # "restarted"
        status, _, body = call(f"{base}/api/me", token=drew)
        assert status == 200 and json.loads(body)["id"] == result.people["drew"]
        assert call(f"{base}/api/me", token=alice)[0] == 401  # signed out: sign in again
        assert call(f"{base}/discover", token=alice)[0] == 302


def test_remembered_sessions_run_out_and_a_broken_file_just_means_signing_in_again(tmp_path):
    now = [1000.0]
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path, clock=lambda: now[0])
    token, forgotten = store.create("drew", ttl=60), store.create("dev")  # no ttl: memory only
    assert SessionStore(path=path, clock=lambda: now[0]).get(token).person_id == "drew"
    assert SessionStore(path=path, clock=lambda: now[0]).get(forgotten) is None
    now[0] += 61
    assert SessionStore(path=path, clock=lambda: now[0]).get(token) is None
    path.write_text("{not json")
    assert SessionStore(path=path).get(token) is None
    store.end_all("drew")
    assert json.loads(path.read_text()) == {"sessions": {}}


# --- Duo, admins only ------------------------------------------------------------------


def test_duo_gate_allows_a_push_or_a_passcode_and_nothing_else():
    push = FakeDuo()
    gate(push).approve("boss")
    assert push.calls == [("preauth", "boss"), ("push", "boss", None)]
    code = FakeDuo()
    gate(code).approve("boss", "123456")
    assert code.calls[-1] == ("passcode", "boss", "123456")
    gate(FakeDuo(preauth="allow")).approve("boss")  # Duo's own bypass policy
    for fake in (FakeDuo(auth="deny"), FakeDuo(preauth="deny"), FakeDuo(preauth="enroll"),
                 FakeDuo(preauth="???"), FakeDuo(error=OSError("no network"))):
        with pytest.raises(DuoError):
            gate(fake).approve("boss")
    with pytest.raises(DuoError) as no_push:
        gate(FakeDuo(devices=[{"capabilities": ["sms"]}])).approve("boss")
    assert no_push.value.needs_passcode
    with pytest.raises(DuoError) as wrong_code:
        gate(FakeDuo(auth="deny")).approve("boss", "000000")
    assert wrong_code.value.needs_passcode


def test_duo_settings_file_round_trips_and_is_private(tmp_path):
    path = tmp_path / "duo.local.json"
    assert DuoGate.load(path) is None  # not set up
    save_settings(SETTINGS, path)
    assert path.stat().st_mode & 0o077 == 0 and DuoGate.load(path) is not None
    path.write_text('{"integration_key": "a"}')
    with pytest.raises(DuoError):
        DuoGate.load(path)


def test_an_admin_needs_duo_after_the_password_and_students_never_do():
    fake = FakeDuo()
    with running(duo=gate(fake)) as (base, result, srv):
        sign_in(base, DREW)
        assert fake.calls == []  # ordinary sign-ins never touch Duo
        status, _, cookie = admin_post(base, username="boss", password="wrong password!")
        assert status == 401 and not cookie and fake.calls == []  # no push before the password
        status, me, cookie = admin_post(base, username="boss", password=PASSWORD)
        assert status == 200 and me["id"] == result.people["pat"] and cookie
        assert fake.calls == [("preauth", "Boss"), ("push", "Boss", None)]
        assert srv.accounts.audit.entries()[-1]["detail"] == "password + duo"


def test_a_refused_or_unreachable_duo_keeps_the_admin_out():
    for fake in (FakeDuo(auth="deny"), FakeDuo(error=OSError("down"))):
        with running(duo=gate(fake)) as (base, _, srv):
            status, data, cookie = admin_post(base, username="boss", password=PASSWORD)
            assert status == 401 and not cookie and data["duo"] is True
            assert srv.accounts.audit.entries()[-1]["detail"] == "duo"
            assert srv.sessions.count_for(srv.app.admin_account_id(PAT)) == 0


def test_a_passcode_is_asked_for_when_there_is_no_push_and_a_duo_name_can_differ():
    fake = FakeDuo(devices=[])
    named = AdminCredential("Boss", PAT, hash_password(PASSWORD), duo_username="kj-duo")
    with running(duo=gate(fake), credential=named) as (base, _, _):
        status, data, cookie = admin_post(base, username="boss", password=PASSWORD)
        assert status == 401 and data["needs_passcode"] and not cookie
        status, _, cookie = admin_post(base, username="boss", password=PASSWORD, passcode="123456")
        assert status == 200 and cookie and fake.calls[-1] == ("passcode", "kj-duo", "123456")
        assert admin_post(base, username="boss", password=PASSWORD, passcode="1" * 50)[0] == 401
