"""Auth для admin panel: bcrypt + signed session cookie через itsdangerous."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

SESSION_COOKIE = "admin_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 днів


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    secret = settings.admin_session_secret
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "ADMIN_SESSION_SECRET is missing or too short (need ≥32 chars). "
            "Generate one: `openssl rand -hex 32` and add to .env"
        )
    return URLSafeTimedSerializer(secret_key=secret, salt="admin-session-v1")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: str, email: str) -> str:
    """Створює signed session token з user_id + email + timestamp."""
    payload = {
        "uid": user_id,
        "email": email,
        "iat": datetime.utcnow().isoformat(),
    }
    return _serializer().dumps(json.dumps(payload))


def decode_session_token(token: str | None) -> dict | None:
    """Розкодовує і валідує session token. None якщо невалідний/прострочений."""
    if not token:
        return None
    try:
        raw = _serializer().loads(token, max_age=SESSION_TTL_SECONDS)
        return json.loads(raw)
    except (BadSignature, SignatureExpired, json.JSONDecodeError, ValueError):
        return None
