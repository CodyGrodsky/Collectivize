"""Sending the sign-in code to someone's Northeastern mailbox.

Two ways out:
- `SmtpMailer`: real email, through the mail server in
  `config/mail.local.json` (git-ignored, written by `scripts/set_mail.py`).
- `ConsoleMailer`: no email at all. The code is printed in the server's
  terminal. Used when mail isn't set up (local development), and always for
  the fictional demo accounts (`name.demo@northeastern.edu`), which have no
  mailbox to send to.

The message holds the code and nothing else about the person.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from backend.config import PROJECT_ROOT

log = logging.getLogger(__name__)

MAIL_PATH = PROJECT_ROOT / "config" / "mail.local.json"
SMTP_TIMEOUT_SECONDS = 15
DEMO_SUFFIX = ".demo"  # local parts ending in this are fictional accounts


class MailError(Exception):
    """The message couldn't be sent."""


class Mailer(Protocol):
    def send_code(self, to: str, code: str, minutes: int) -> None: ...

    def send_notice(self, to: str, subject: str, text: str) -> None: ...


def is_demo_address(address: str) -> bool:
    return address.partition("@")[0].endswith(DEMO_SUFFIX)


class ConsoleMailer:
    """Shows the code in the server's terminal instead of emailing it."""

    def send_code(self, to: str, code: str, minutes: int) -> None:
        log.warning("SIGN-IN CODE for %s: %s  (valid %d minutes; shown here because "
                    "no email was sent)", to, code, minutes)

    def send_notice(self, to: str, subject: str, text: str) -> None:
        log.warning("NOTICE for %s (not emailed): %s", to, subject)


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str  # the From address


class SmtpMailer:
    def __init__(self, settings: MailSettings, demo: Mailer | None = None) -> None:
        self._settings = settings
        self._demo = demo or ConsoleMailer()

    def send_code(self, to: str, code: str, minutes: int) -> None:
        if is_demo_address(to):
            self._demo.send_code(to, code, minutes)  # no such mailbox
            return
        self._send(to, f"{code} is your Collectivize code",
                   f"Your Collectivize code is {code}.\n\n"
                   f"It works once and for {minutes} minutes. If you didn't ask for it, ignore "
                   "this email: nothing happens without the code.\n")

    def send_notice(self, to: str, subject: str, text: str) -> None:
        if is_demo_address(to):
            self._demo.send_notice(to, subject, text)
            return
        self._send(to, subject, text)

    def _send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._settings.sender
        message["To"] = to
        message.set_content(text)
        settings = self._settings
        try:
            context = ssl.create_default_context()
            if settings.port == 465:
                server = smtplib.SMTP_SSL(settings.host, settings.port,
                                          timeout=SMTP_TIMEOUT_SECONDS, context=context)
            else:
                server = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS)
            with server:
                if settings.port != 465:
                    server.starttls(context=context)  # never send the password in the clear
                if settings.username:
                    server.login(settings.username, settings.password)
                server.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            log.warning("couldn't send an email: %s", type(error).__name__)
            raise MailError("the email couldn't be sent") from None


def load_mailer(path: Path = MAIL_PATH) -> Mailer:
    """The SMTP mailer from the settings file, or the console one without it."""
    if not path.exists():
        return ConsoleMailer()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = MailSettings(str(data["host"]), int(data["port"]), str(data.get("username", "")),
                                str(data.get("password", "")), str(data["sender"]))
    except (KeyError, TypeError, ValueError):
        raise MailError(f"{path.name} isn't a valid mail settings file") from None
    return SmtpMailer(settings)


def save_settings(settings: MailSettings, path: Path = MAIL_PATH) -> None:
    """Write the mail settings file (readable only by you)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
