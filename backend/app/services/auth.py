from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Request

from app.config import get_settings


SESSION_COOKIE = "drisk_session"
PBKDF2_ITERATIONS = 260_000


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str) -> bool:
    settings = get_settings()
    encoded = settings.app_password_hash.strip()
    if encoded:
        try:
            algorithm, iterations, salt, expected = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                _b64decode(salt),
                int(iterations),
            )
            return hmac.compare_digest(_b64encode(digest), expected)
        except (TypeError, ValueError):
            return False
    return bool(settings.app_password) and hmac.compare_digest(
        password, settings.app_password
    )


def create_session(username: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "exp": int(time.time()) + settings.session_ttl_hours * 3600,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(
            settings.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"{encoded}.{signature}"


def read_session(token: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    settings = get_settings()
    encoded, signature = token.rsplit(".", 1)
    expected = _b64encode(
        hmac.new(
            settings.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        return None
    if not str(payload.get("sub") or "").strip():
        return None
    return payload


def actor_from_request(request: Request) -> str:
    actor = getattr(request.state, "user", "")
    return str(actor or "local-dev")


if __name__ == "__main__":
    import getpass

    print(hash_password(getpass.getpass("Password: ")))
