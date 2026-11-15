"""Turn on Duo two-factor for admin sign-ins.

    uv run python scripts/set_duo.py            # asks for the three values, then tests them
    uv run python scripts/set_duo.py --check    # test the saved values
    uv run python scripts/set_duo.py --off      # remove Duo (admin sign-in is password only again)

In the Duo Admin Panel, add an application of type "Auth API" and copy its
integration key, secret key and API hostname. Each admin also needs a Duo
user whose username matches their admin username here (or the one given
with `set_admin_password.py --duo-username`).

Writes config/duo.local.json (git-ignored, readable only by you).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.duo import DUO_PATH, DuoError, DuoGate, DuoSettings, save_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="test the saved values against Duo")
    parser.add_argument("--off", action="store_true", help="remove the Duo settings")
    args = parser.parse_args(argv)
    if args.off:
        DUO_PATH.unlink(missing_ok=True)
        sys.stdout.write("Duo is off: admin sign-in is password only. Restart the server.\n")
        return 0
    try:
        if not args.check:
            settings = DuoSettings(
                input("Integration key: ").strip(),
                getpass.getpass("Secret key (hidden): ").strip(),
                input("API hostname (api-XXXXXXXX.duosecurity.com): ").strip(),
            )
            if not all((settings.integration_key, settings.secret_key, settings.api_hostname)):
                sys.stderr.write("All three values are needed. Nothing was saved.\n")
                return 1
            save_settings(settings)
            sys.stdout.write(f"Saved to {DUO_PATH}.\n")
        gate = DuoGate.load()
        if gate is None:
            sys.stderr.write("Duo isn't set up. Run this without --check first.\n")
            return 1
        gate.check()
    except DuoError as error:
        sys.stderr.write(f"{error.message}\nThe values are saved but did not work: check them "
                         "in the Duo Admin Panel, or run with --off.\n")
        return 1
    sys.stdout.write("Duo answered: the keys work. Restart the server; admin sign-ins now "
                     "need Duo approval.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
