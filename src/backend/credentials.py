"""Where secrets come from. Never from source code or files in the repo.

The Anthropic API key is looked up in this order:
1. The ANTHROPIC_API_KEY environment variable (servers, CI).
2. On macOS, the login Keychain item "collectivize-anthropic-api-key"
   (local development). Store or replace it with:
       security add-generic-password -U -a "$USER" \\
           -s collectivize-anthropic-api-key -w
   (with no value after -w, it prompts, so the key never appears in your
   shell history), and remove it with:
       security delete-generic-password -s collectivize-anthropic-api-key
3. Otherwise None, and the Anthropic SDK falls back to its own sources
   (for example an `ant auth login` profile).

The key is returned to the caller only; it is never logged or printed.
"""

from __future__ import annotations

import os
import subprocess
import sys

API_KEY_ENV = "ANTHROPIC_API_KEY"
KEYCHAIN_SERVICE = "collectivize-anthropic-api-key"
KEYCHAIN_TIMEOUT_SECONDS = 5


def anthropic_api_key() -> str | None:
    from_env = os.environ.get(API_KEY_ENV, "").strip()
    if from_env:
        return from_env
    if sys.platform != "darwin":
        return None
    return _from_keychain(KEYCHAIN_SERVICE)


def _from_keychain(service: str) -> str | None:
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None  # not stored, or access denied
    return result.stdout.strip() or None
