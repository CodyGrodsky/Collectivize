"""Set up the mail server that sends sign-in codes.

    uv run python scripts/set_mail.py                             # asks for the settings
    uv run python scripts/set_mail.py --test you@northeastern.edu # send yourself a test code
    uv run python scripts/set_mail.py --off                       # stop emailing (codes show in the terminal)

Any SMTP account works: for example a Gmail or Outlook account with an
"app password" (smtp.gmail.com or smtp.office365.com, port 587). Writes
config/mail.local.json (git-ignored, readable only by you).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backend.mailer import (
    MAIL_PATH,
    MailError,
    MailSettings,
    SmtpMailer,
    load_mailer,
    save_settings,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--test", metavar="EMAIL", help="send a test code to this address")
    parser.add_argument("--off", action="store_true", help="remove the mail settings")
    args = parser.parse_args(argv)
    if args.off:
        MAIL_PATH.unlink(missing_ok=True)
        sys.stdout.write("Mail is off: sign-in codes will show in the server's terminal.\n")
        return 0
    try:
        if not args.test:
            host = input("SMTP host (e.g. smtp.gmail.com): ").strip()
            port = int(input("Port [587]: ").strip() or "587")
            username = input("Username (usually the full email address): ").strip()
            password = getpass.getpass("Password or app password (hidden): ")
            sender = input(f"From address [{username}]: ").strip() or username
            if not host or not sender:
                sys.stderr.write("A host and a From address are needed. Nothing was saved.\n")
                return 1
            save_settings(MailSettings(host, port, username, password, sender))
            sys.stdout.write(f"Saved to {MAIL_PATH}. Now run this again with --test <your email>.\n")
            return 0
        mailer = load_mailer()
        if not isinstance(mailer, SmtpMailer):
            sys.stderr.write("Mail isn't set up. Run this without --test first.\n")
            return 1
        mailer.send_code(args.test.strip().lower(), "000000", 10)
    except ValueError:
        sys.stderr.write("The port must be a number. Nothing was saved.\n")
        return 1
    except MailError as error:
        sys.stderr.write(f"{error}. Check the host, port, username and password.\n")
        return 1
    sys.stdout.write(f"Sent a test message to {args.test}. Restart the server to start emailing codes.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
