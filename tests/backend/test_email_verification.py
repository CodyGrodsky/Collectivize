"""Signing in needs a code sent to the Northeastern address, and one
mailbox is one account."""

import json
import re
import threading
from contextlib import contextmanager
from http import HTTPStatus

import pytest

from backend import config
from backend.accounts import AccountsApi
from backend.admin_auth import AdminAuth, AdminCredential, hash_password
from backend.app_api import AppApi, canonical_email
from backend.config import Environment
from backend.demo_api import ApiError
from backend.email_verification import (
    CODE_SECONDS,
    MAX_SENDS_PER_HOUR,
    MAX_TRIES,
    RESEND_SECONDS,
    EmailVerifier,
    VerificationError,
)
from backend.graph import CollectivizeGraph
from backend.mailer import (
    ConsoleMailer,
    MailError,
    MailSettings,
    SmtpMailer,
    is_demo_address,
    load_mailer,
    save_settings,
)
from backend.objects import Person
from backend.seed import seed_mock_data
from backend.server import DiscoverApi, serve
from backend.sessions import SessionStore

from .conftest import NOW
from .test_access import call

DREW = "drew.demo@northeastern.edu"
EMAILED = True  # the test outbox counts as real mail (for non-demo addresses)
NEW = "new.student@northeastern.edu"
PAT = "pat.demo@northeastern.edu"


class Outbox:
    """A mailer that keeps what it was asked to send."""

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_code(self, to, code, minutes):
        if self.fail:
            raise MailError("down")
        self.sent.append((to, code))

    def code_for(self, address):
        return next(code for to, code in reversed(self.sent) if to == address)


@contextmanager
def running(outbox, *, admin_auth=None, clock=None):
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    verifier = EmailVerifier(outbox, **({"clock": clock} if clock else {}))
    srv = serve(DiscoverApi(factory, clock=lambda: NOW), port=0, app=AppApi(factory, clock=lambda: NOW),
                sessions=SessionStore(), verifier=verifier, admin_auth=admin_auth)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", graph, result
    finally:
        srv.shutdown()
        srv.server_close()


def post(base, path, body):
    status, headers, raw = call(f"{base}{path}", body)
    return status, json.loads(raw), headers.get("Set-Cookie", "")


# --- the verifier ----------------------------------------------------------------------


def test_a_code_is_six_digits_works_once_and_only_for_its_address():
    outbox = Outbox()
    verifier = EmailVerifier(outbox)
    verifier.start(DREW)
    code = outbox.code_for(DREW)
    assert re.fullmatch(r"\d{6}", code)
    with pytest.raises(VerificationError):
        verifier.check(NEW, code)  # someone else's address
    verifier.check(DREW, f" {code} ")
    with pytest.raises(VerificationError):
        verifier.check(DREW, code)  # already used


def test_wrong_guesses_throw_the_code_away():
    outbox = Outbox()
    verifier = EmailVerifier(outbox)
    verifier.start(DREW)
    code = outbox.code_for(DREW)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_TRIES):
        with pytest.raises(VerificationError):
            verifier.check(DREW, wrong)
    with pytest.raises(VerificationError) as gone:
        verifier.check(DREW, code)  # even the right one, now
    assert "new one" in gone.value.message


def test_codes_run_out_and_requests_are_rate_limited():
    now = [0.0]
    outbox = Outbox()
    verifier = EmailVerifier(outbox, clock=lambda: now[0])
    verifier.start(DREW)
    with pytest.raises(VerificationError) as soon:
        verifier.start(DREW)  # asked again straight away
    assert soon.value.status == HTTPStatus.TOO_MANY_REQUESTS and len(outbox.sent) == 1
    first = outbox.code_for(DREW)
    now[0] += RESEND_SECONDS + 1
    verifier.start(DREW)
    with pytest.raises(VerificationError):
        verifier.check(DREW, first)  # replaced by the newer code
    now[0] += CODE_SECONDS + 1
    with pytest.raises(VerificationError):
        verifier.check(DREW, outbox.code_for(DREW))  # too old
    for _ in range(MAX_SENDS_PER_HOUR - 2):
        now[0] += RESEND_SECONDS + 1
        verifier.start(DREW)
    now[0] += RESEND_SECONDS + 1
    with pytest.raises(VerificationError):
        verifier.start(DREW)  # the hourly limit
    verifier.start(NEW)  # other addresses are unaffected
    now[0] += 3601
    verifier.start(DREW)


def test_a_mail_failure_is_reported_and_leaves_no_code():
    verifier = EmailVerifier(Outbox(fail=True))
    with pytest.raises(VerificationError) as failed:
        verifier.start(DREW)
    assert failed.value.status == HTTPStatus.SERVICE_UNAVAILABLE
    with pytest.raises(VerificationError):
        verifier.check(DREW, "000000")


# --- over HTTP -------------------------------------------------------------------------


def test_typing_an_email_does_not_sign_you_in_the_code_does():
    outbox = Outbox()
    with running(outbox) as (base, _, result):
        status, data, cookie = post(base, "/api/signin", {"email": "Drew.Demo@northeastern.edu "})
        # A demo account has no mailbox: the page is told its code wasn't emailed.
        assert status == 200 and data == {"verify": True, "digits": 6, "emailed": False} and not cookie
        assert call(f"{base}/api/me")[0] == 401
        status, data, cookie = post(base, "/api/signin/verify", {"email": DREW, "code": "999999"})
        assert status == 401 and not cookie and outbox.code_for(DREW) != "999999"
        status, me, cookie = post(base, "/api/signin/verify", {"email": DREW, "code": outbox.code_for(DREW)})
        assert status == 200 and me["id"] == result.people["drew"] and "Max-Age=2592000" in cookie
        assert post(base, "/api/signin/verify", {"email": DREW, "code": outbox.code_for(DREW)})[0] == 401


def test_a_new_account_exists_only_after_its_code_is_entered():
    outbox = Outbox()
    with running(outbox) as (base, graph, _):
        before = len(graph.store.nodes(Person))
        assert post(base, "/api/signin", {"email": NEW})[1] == {"verify": True, "digits": 6, "emailed": EMAILED}
        assert len(graph.store.nodes(Person)) == before  # same answer, but nothing created yet
        status, me, _ = post(base, "/api/signin/verify", {"email": NEW, "code": outbox.code_for(NEW)})
        assert status == 200 and len(graph.store.nodes(Person)) == before + 1
        assert graph.store.node(me["id"], Person).email == NEW


@pytest.mark.parametrize("body", [
    {"email": "someone@gmail.com"}, {"email": "not an email"}, {"email": 5}, {},
])
def test_only_northeastern_addresses_are_sent_a_code(body):
    outbox = Outbox()
    with running(outbox) as (base, _, _):
        assert post(base, "/api/signin", body)[0] in (400, 403) and outbox.sent == []
        assert post(base, "/api/signin/verify", {**body, "code": "123456"})[0] in (400, 403)


def test_bad_codes_and_missing_fields_are_refused():
    outbox = Outbox()
    with running(outbox) as (base, _, _):
        post(base, "/api/signin", {"email": DREW})
        for code in (None, "", 123456, "1" * 50, ["123456"]):
            status, _, cookie = post(base, "/api/signin/verify", {"email": DREW, "code": code})
            assert status == 401 and not cookie


def test_admin_accounts_are_never_opened_by_an_emailed_code():
    outbox = Outbox()
    auth = AdminAuth((AdminCredential("boss", PAT, hash_password("correct horse battery")),))
    with running(outbox, admin_auth=auth) as (base, _, _):
        assert post(base, "/api/signin", {"email": PAT})[1] == {"admin": True} and outbox.sent == []
        status, _, cookie = post(base, "/api/signin/verify", {"email": PAT, "code": "123456"})
        assert status == 401 and not cookie


def test_without_a_verifier_the_verify_route_does_not_exist():
    graph = CollectivizeGraph()
    seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    srv = serve(DiscoverApi(factory), port=0, app=AppApi(factory))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert post(base, "/api/signin/verify", {"email": DREW, "code": "123456"})[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_verification_is_on_by_default():
    assert config.EMAIL_VERIFICATION is True


# --- one mailbox, one account ----------------------------------------------------------


@pytest.mark.parametrize(("typed", "stored"), [
    ("Drew.Demo@Northeastern.edu", DREW), (" drew.demo+clubs@northeastern.edu ", DREW),
    ("drew.demo+a+b@northeastern.edu", DREW), ("new.student@northeastern.edu", NEW),
])
def test_case_and_plus_tags_are_the_same_mailbox(typed, stored):
    assert canonical_email(typed) == AppApi.check_email(typed) == stored


def test_a_plus_tag_signs_in_to_the_existing_account_not_a_second_one():
    outbox = Outbox()
    with running(outbox) as (base, graph, result):
        before = len(graph.store.nodes(Person))
        post(base, "/api/signin", {"email": "drew.demo+second@northeastern.edu"})
        assert outbox.sent[-1][0] == DREW  # the code goes to the real mailbox
        status, me, _ = post(base, "/api/signin/verify",
                             {"email": "DREW.demo+second@northeastern.edu", "code": outbox.code_for(DREW)})
        assert status == 200 and me["id"] == result.people["drew"]
        assert len(graph.store.nodes(Person)) == before


def test_an_admin_cannot_create_a_second_account_for_a_mailbox():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    accounts = AccountsApi(factory, SessionStore(), clock=lambda: NOW)
    with pytest.raises(ApiError) as taken:
        accounts.admin_create(result.people["pat"], {"email": "Drew.Demo+2@northeastern.edu"})
    assert taken.value.status == HTTPStatus.CONFLICT
    made = accounts.admin_create(result.people["pat"], {"email": "Fresh+tag@northeastern.edu"})
    assert made["email"] == "fresh@northeastern.edu"


# --- mailers ---------------------------------------------------------------------------


def test_without_mail_settings_codes_go_to_the_terminal(tmp_path, caplog):
    mailer = load_mailer(tmp_path / "mail.local.json")
    assert isinstance(mailer, ConsoleMailer)
    with caplog.at_level("WARNING"):
        mailer.send_code(DREW, "123456", 10)
    assert "123456" in caplog.text and DREW in caplog.text


def test_mail_settings_round_trip_privately_and_demo_accounts_are_never_emailed(tmp_path):
    path = tmp_path / "mail.local.json"
    save_settings(MailSettings("smtp.example.org", 587, "sender@example.org", "app password", "sender@example.org"), path)
    assert path.stat().st_mode & 0o077 == 0 and isinstance(load_mailer(path), SmtpMailer)
    demo = Outbox()
    unreachable = SmtpMailer(MailSettings("127.0.0.1", 9, "", "", "x@example.org"), demo=demo)
    unreachable.send_code(DREW, "123456", 10)  # a fictional mailbox: no connection is made
    assert demo.sent == [(DREW, "123456")] and is_demo_address(DREW) and not is_demo_address(NEW)
    with pytest.raises(MailError):
        unreachable.send_code(NEW, "123456", 10)  # a real address: the send fails loudly
    path.write_text('{"host": "x"}')
    with pytest.raises(MailError):
        load_mailer(path)
