"""Admin powers and the owner: what each admin may do is chosen by the
owner; admin accounts are the owner's alone to manage; and a deleted or
signed-out account's browsers are sent home."""

import json
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest

from backend.accounts import AccountsApi
from backend.admin_auth import (
    AdminAuth,
    AdminCredential,
    hash_password,
    save_credential,
)
from backend.app_api import AppApi
from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.objects import DomainRuleError, Person
from backend.objects.person import ADMIN_POWERS
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.sessions import SessionStore

from .conftest import NOW
from .test_access import call, sign_in

PAT = "pat.demo@northeastern.edu"  # the owner
CASEY = "casey.demo@northeastern.edu"  # made a platform admin below
DREW = "drew.demo@northeastern.edu"
ALICE = "alice.demo@northeastern.edu"


@pytest.fixture
def world():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    sessions = SessionStore()
    accounts = AccountsApi(factory, sessions, clock=lambda: NOW, owners=frozenset({PAT}))
    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0,
                app=AppApi(factory, clock=lambda: NOW), sessions=sessions, accounts=accounts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    owner, _ = sign_in(base, PAT)
    post(base, "/api/admin/admins", {"person_id": result.people["casey"], "is_admin": True}, owner)
    admin, _ = sign_in(base, CASEY)
    yield base, graph, result, owner, admin, sessions
    srv.shutdown()
    srv.server_close()


def post(base, path, body, token):
    status, _, raw = call(f"{base}{path}", body, token=token)
    return status, json.loads(raw)


def get(base, path, token):
    status, _, raw = call(f"{base}{path}", token=token)
    return status, json.loads(raw)


def give(world, *powers):
    base, _, result, owner, *_ = world
    body = {"person_id": result.people["casey"], "powers": list(powers)}
    assert post(base, "/api/admin/admins", body, owner)[0] == 200


# --- powers ----------------------------------------------------------------------------


def test_a_new_admin_can_only_look_at_the_list(world):
    base, graph, result, _, admin, _ = world
    drew = result.people["drew"]
    status, me = get(base, "/api/me", admin)
    assert me["is_platform_admin"] and not me["is_owner"] and me["powers"] == []
    status, users = get(base, "/api/admin/users?q=drew", admin)
    assert status == 200 and users["users"][0]["email"] is None  # no emails without view_info
    assert get(base, "/api/admin/users?q=drew.demo@", admin)[1]["total"] == 0  # nor searching by them
    assert get(base, f"/api/admin/account?id={drew}", admin)[0] == 403
    assert post(base, "/api/admin/view-as", {"person_id": drew}, admin)[0] == 403
    assert post(base, "/api/admin/account", {"person_id": drew, "bio": "x"}, admin)[0] == 403
    assert post(base, "/api/admin/account/create", {"email": "n@northeastern.edu"}, admin)[0] == 403
    assert post(base, "/api/admin/account/signout", {"person_id": drew}, admin)[0] == 403
    assert post(base, "/api/admin/account/delete", {"person_id": drew, "confirm": DREW}, admin)[0] == 403
    assert graph.store.has_node(drew)


@pytest.mark.parametrize(("power", "path", "body"), [
    ("view_as", "/api/admin/view-as", {}),
    ("edit", "/api/admin/account", {"bio": "by staff"}),
    ("sign_out", "/api/admin/account/signout", {}),
    ("delete", "/api/admin/account/delete", {"confirm": DREW}),
])
def test_each_power_opens_exactly_its_own_action(world, power, path, body):
    base, _, result, _, admin, _ = world
    request = {"person_id": result.people["drew"], **body}
    give(world, *(p for p in ADMIN_POWERS if p != power))  # everything but this one
    assert post(base, path, request, admin)[0] == 403
    give(world, power)  # only this one: effective on the very next request
    assert post(base, path, request, admin)[0] == 200


def test_view_info_and_create_powers(world):
    base, _, result, _, admin, _ = world
    give(world, "view_info", "create")
    status, account = get(base, f"/api/admin/account?id={result.people['drew']}", admin)
    assert status == 200 and account["email"] == DREW
    assert get(base, "/api/admin/users?q=drew.demo@", admin)[1]["users"][0]["email"] == DREW
    assert post(base, "/api/admin/account/create", {"email": "n@northeastern.edu"}, admin)[0] == 200


def test_only_the_owner_changes_a_sign_in_email(world):
    base, graph, result, owner, admin, _ = world
    drew = result.people["drew"]
    give(world, *ADMIN_POWERS)
    change = {"person_id": drew, "email": "taken.over@northeastern.edu"}
    assert post(base, "/api/admin/account", change, admin)[0] == 403
    assert graph.store.node(drew, Person).email == DREW
    assert post(base, "/api/admin/account", change, owner)[0] == 200


# --- the owner ---------------------------------------------------------------------------


def test_admin_accounts_are_the_owners_alone_to_manage(world):
    base, graph, result, owner, admin, _ = world
    give(world, *ADMIN_POWERS)  # even with every power
    pat, casey = result.people["pat"], result.people["casey"]
    for target, email in ((pat, PAT), (casey, CASEY)):
        assert get(base, f"/api/admin/account?id={target}", admin)[0] == 403
        assert post(base, "/api/admin/view-as", {"person_id": target}, admin)[0] == 403
        assert post(base, "/api/admin/account", {"person_id": target, "bio": "x"}, admin)[0] == 403
        assert post(base, "/api/admin/account/signout", {"person_id": target}, admin)[0] == 403
        refused = post(base, "/api/admin/account/delete", {"person_id": target, "confirm": email}, admin)[0]
        assert refused == (400 if target == casey else 403)  # yourself: use your Account page
    assert graph.store.has_node(casey)
    assert get(base, "/api/me", owner)[0] == 200 and graph.store.has_node(pat)
    assert get(base, f"/api/admin/account?id={casey}", owner)[0] == 200


def test_an_admin_cannot_manage_admins_or_raise_their_own_powers(world):
    base, graph, result, _, admin, _ = world
    give(world, *ADMIN_POWERS)
    casey, drew = result.people["casey"], result.people["drew"]
    assert get(base, "/api/admin/admins", admin)[0] == 403
    for body in ({"person_id": drew, "is_admin": True}, {"person_id": casey, "powers": ["delete"]},
                 {"person_id": result.people["pat"], "is_admin": False}):
        assert post(base, "/api/admin/admins", body, admin)[0] == 403
    assert post(base, "/api/profile", {"admin_powers": ["delete"], "is_platform_admin": True}, admin)[0] == 200
    assert not graph.store.node(drew, Person).is_platform_admin


def test_the_owner_lists_admins_and_sets_powers(world):
    base, graph, result, owner, _, _ = world
    casey = result.people["casey"]
    status, data = post(base, "/api/admin/admins", {"person_id": casey, "powers": ["delete", "view_info"]}, owner)
    assert status == 200 and data["powers"] == ["delete", "view_info"]
    assert graph.store.node(casey, Person).admin_powers == ("view_info", "delete")
    status, listing = get(base, "/api/admin/admins", owner)
    by_id = {a["id"]: a for a in listing["admins"]}
    assert listing["all_powers"] == list(ADMIN_POWERS) and set(by_id) == {casey, result.people["pat"]}
    assert by_id[result.people["pat"]]["is_owner"] and by_id[result.people["pat"]]["is_you"]
    assert sorted(by_id[result.people["pat"]]["powers"]) == sorted(ADMIN_POWERS)
    for bad in ({"powers": ["fly"]}, {"powers": "delete"}, {"powers": ["delete", "delete"]},
                {"is_admin": "yes"}, {"powers": [], "role": "owner"}):
        assert post(base, "/api/admin/admins", {"person_id": casey, **bad}, owner)[0] == 400, bad


def test_removing_an_admin_clears_their_powers_and_signs_them_out(world):
    base, graph, result, owner, admin, _ = world
    give(world, *ADMIN_POWERS)
    casey = result.people["casey"]
    assert post(base, "/api/admin/admins", {"person_id": casey, "is_admin": False}, owner)[0] == 200
    person = graph.store.node(casey, Person)
    assert not person.is_platform_admin and person.admin_powers == ()
    assert get(base, "/api/me", admin)[0] == 401


def test_the_owner_cannot_be_demoted_or_deleted_from_the_app(world):
    base, graph, result, owner, _, _ = world
    pat = result.people["pat"]
    assert post(base, "/api/admin/admins", {"person_id": pat, "is_admin": False}, owner)[0] == 400
    status, data = post(base, "/api/account/delete", {"confirm": PAT}, owner)
    assert status == 400 and "owner" in data["error"]
    assert graph.store.node(pat, Person).is_platform_admin


def test_powers_belong_to_admins_only():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    drew = graph.store.node(result.people["drew"], Person)
    with pytest.raises(DomainRuleError):
        replace(drew, admin_powers=("delete",))
    with pytest.raises(DomainRuleError):
        replace(drew, is_platform_admin=True, admin_powers=("fly",))


def test_the_owner_role_comes_from_the_credentials_file(tmp_path):
    path = tmp_path / "admin.local.json"
    save_credential(AdminCredential("boss", PAT, hash_password("a long password!"), "owner"), path)
    save_credential(AdminCredential("helper", CASEY, hash_password("a long password!")), path)
    loaded = AdminAuth.load(path)
    assert loaded.owner_emails == {PAT} and loaded.emails == {PAT, CASEY}
    assert loaded.credential("BOSS").role == "owner"


# --- sent home ---------------------------------------------------------------------------


def test_a_deleted_account_is_sent_home_on_its_next_request(world):
    base, _, result, owner, _, _ = world
    drew_token, _ = sign_in(base, DREW)
    deleted = post(base, "/api/admin/account/delete", {"person_id": result.people["drew"], "confirm": DREW}, owner)
    assert deleted[0] == 200
    status, headers, body = call(f"{base}/api/discover", token=drew_token)
    assert status == 401 and json.loads(body)["reason"] == "deleted"
    assert "Max-Age=0" in headers["Set-Cookie"]  # the dead cookie is cleared
    status, headers, _ = call(f"{base}/src/frontend/discover.html", token=drew_token)
    assert status == 302 and headers["Location"].endswith("index.html?notice=deleted")
    assert json.loads(call(f"{base}/api/session", token=drew_token)[2]) == {"signed_in": False, "reason": "deleted"}


def test_a_deleted_account_is_not_signed_in_as_someone_else_with_login_off():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=SessionStore(), auto_login_email=DREW)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        alice, _ = sign_in(base, ALICE)
        assert post(base, "/api/account/delete", {"confirm": ALICE}, alice)[0] == 200
        status, _, body = call(f"{base}/api/me", token=alice)
        assert status == 401 and json.loads(body)["reason"] == "deleted"  # not quietly Drew
        assert call(f"{base}/src/frontend/discover.html", token=alice)[0] == 302
        assert get(base, "/api/me", None)[1]["id"] == result.people["drew"]  # a fresh browser still is
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_admin_sign_in_ends_by_itself():
    now = [0.0]
    sessions = SessionStore(clock=lambda: now[0])
    token, forever = sessions.create("admin", ttl=60), sessions.create("student")
    assert sessions.get(token).person_id == "admin"
    now[0] = 61
    assert sessions.get(token) is None and sessions.ended_reason(token) == "expired"
    assert sessions.get(forever).person_id == "student"
