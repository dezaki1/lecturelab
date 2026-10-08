"""API key storage: Windows Credential Manager via ``keyring``, with env vars as fallback.

Keys are never logged or returned by the API; only a masked hint (last 4 characters) is shown.
"""

from __future__ import annotations

import contextlib
import logging
import os

log = logging.getLogger(__name__)

SERVICE = "LectureLab"
ENV_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def get_key(provider: str) -> str | None:
    env = os.environ.get(ENV_VARS.get(provider, ""), "").strip()
    if env:
        return env
    try:
        import keyring

        value = keyring.get_password(SERVICE, f"{provider}_api_key")
        return value.strip() if value else None
    except Exception as e:  # no keyring backend available
        log.warning("Could not read key from keyring: %s", type(e).__name__)
        return None


def set_key(provider: str, key: str) -> None:
    import keyring

    keyring.set_password(SERVICE, f"{provider}_api_key", key.strip())


def delete_key(provider: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(SERVICE, f"{provider}_api_key")


def key_hint(provider: str) -> dict[str, object]:
    key = get_key(provider)
    source = "env" if os.environ.get(ENV_VARS.get(provider, "")) else "keyring"
    return {
        "set": bool(key),
        "hint": f"…{key[-4:]}" if key else None,
        "source": source if key else None,
    }
