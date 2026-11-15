"""Duo two-factor for admin sign-ins (never for ordinary users).

After an admin's password is right, Duo must also say yes: a push to
their phone, or a passcode from the Duo Mobile app. This uses Duo's Auth
API, which the server calls itself, so it works on a local http://
address (Duo's browser prompt needs a public https:// one).

Settings live in `config/duo.local.json` (git-ignored, written by
`scripts/set_duo.py`): the integration key, secret key and API hostname
of an "Auth API" application in the Duo Admin Panel.

Fail closed: if Duo is set up and can't be reached, or says anything but
"allow", the sign-in is refused. If Duo is not set up at all, admin
sign-in is password only and the server says so loudly at startup.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.config import PROJECT_ROOT

log = logging.getLogger(__name__)

DUO_PATH = PROJECT_ROOT / "config" / "duo.local.json"
DUO_TIMEOUT_SECONDS = 75  # a push waits about a minute for an answer
PUSH_TITLE = "Collectivize admin sign-in"
MAX_PASSCODE = 20


class DuoError(Exception):
    """Duo didn't allow the sign-in. The message is safe to show.
    `needs_passcode`: ask for a passcode and try again."""

    def __init__(self, message: str, *, needs_passcode: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.needs_passcode = needs_passcode


@dataclass(frozen=True)
class DuoSettings:
    integration_key: str
    secret_key: str
    api_hostname: str


def _real_client(settings: DuoSettings) -> Any:
    import duo_client

    return duo_client.Auth(ikey=settings.integration_key, skey=settings.secret_key,
                           host=settings.api_hostname, timeout=DUO_TIMEOUT_SECONDS)


class DuoGate:
    def __init__(
        self, settings: DuoSettings, client_factory: Callable[[DuoSettings], Any] = _real_client
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    @classmethod
    def load(cls, path: Path = DUO_PATH) -> DuoGate | None:
        """The gate from the settings file, or None when Duo isn't set up."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            settings = DuoSettings(*(str(data[key]).strip() for key in
                                     ("integration_key", "secret_key", "api_hostname")))
        except (KeyError, TypeError, ValueError):
            raise DuoError(f"{path.name} isn't a valid Duo settings file") from None
        if not all(asdict(settings).values()):
            raise DuoError(f"{path.name} is missing a value: run scripts/set_duo.py")
        return cls(settings)

    def check(self) -> None:
        """Raises DuoError unless the keys work and Duo answers."""
        self._call(lambda client: client.check())

    def approve(self, username: str, passcode: str | None = None) -> None:
        """Returns only if Duo allows this sign-in; raises DuoError otherwise.
        With no passcode a push is sent and this waits for the answer."""
        ready = self._call(lambda client: client.preauth(username=username))
        result = ready.get("result")
        if result == "allow":
            return  # Duo's own policy lets this user through
        if result == "enroll":
            raise DuoError("This admin isn't enrolled in Duo yet. Enroll the Duo user "
                           f"“{username}”, then sign in again.")
        if result != "auth":
            raise DuoError("Duo refused this sign-in.")
        if passcode:
            answer = self._call(lambda client: client.auth(
                factor="passcode", username=username, passcode=passcode))
        elif any("push" in device.get("capabilities", []) for device in ready.get("devices", [])):
            answer = self._call(lambda client: client.auth(
                factor="push", username=username, device="auto", type=PUSH_TITLE))
        else:
            raise DuoError("Enter a passcode from the Duo Mobile app.", needs_passcode=True)
        if answer.get("result") != "allow":
            raise DuoError("Duo didn't approve this sign-in.", needs_passcode=bool(passcode))

    def _call(self, action: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        try:
            answer = action(self._client_factory(self._settings))
        except DuoError:
            raise
        except Exception as error:  # noqa: BLE001 (network, bad keys, Duo errors: all closed)
            log.warning("Duo call failed: %s", type(error).__name__)
            raise DuoError("Duo couldn't be reached, so admin sign-in is closed for now.") from None
        if not isinstance(answer, dict):
            raise DuoError("Duo gave an answer this app doesn't understand.")
        return answer


def save_settings(settings: DuoSettings, path: Path = DUO_PATH) -> None:
    """Write the Duo settings file (readable only by you)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
