from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.services.auth import create_session, hash_password, read_session, verify_password


def test_password_hash_and_signed_session(monkeypatch) -> None:
    encoded = hash_password("strong-password")
    settings = get_settings()
    monkeypatch.setattr(settings, "app_password", "")
    monkeypatch.setattr(settings, "app_password_hash", encoded)
    monkeypatch.setattr(settings, "session_secret", "s" * 64)
    monkeypatch.setattr(settings, "session_ttl_hours", 12)

    assert verify_password("strong-password") is True
    assert verify_password("wrong-password") is False
    token = create_session("reviewer")
    assert read_session(token)["sub"] == "reviewer"
    assert read_session(token + "tampered") is None
