"""Web chat session: signed cookie з chat_id (uuid) + country + опц. salon_id.

chat_id — стабільний для одного браузера, ідентифікує діалог у ConversationCache.
country — обирається на onboarding (UA/PL/GB).
salon_id — опц., обирається в розмові з агентом.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

SESSION_COOKIE = "chat_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 днів — клієнт повертається до того ж діалогу
SUPPORTED_COUNTRIES = ("ua", "pl", "gb")


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    secret = settings.web_chat_session_secret or settings.admin_session_secret
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "WEB_CHAT_SESSION_SECRET (або ADMIN_SESSION_SECRET) is missing or too short "
            "(need ≥32 chars). Generate: `openssl rand -hex 32`"
        )
    return URLSafeTimedSerializer(secret_key=secret, salt="chat-session-v1")


def new_chat_id() -> str:
    return uuid.uuid4().hex


def create_session_cookie(
    chat_id: str,
    country: str | None = None,
    city: str | None = None,
    salon_id: str | None = None,
) -> str:
    """Signed token: {chat_id, country, city, salon_id, iat}."""
    payload = {
        "cid": chat_id,
        "country": country,
        "city": city,
        "salon_id": salon_id,
        "iat": datetime.utcnow().isoformat(),
    }
    return _serializer().dumps(json.dumps(payload))


def decode_session_cookie(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        raw = _serializer().loads(token, max_age=SESSION_TTL_SECONDS)
        data = json.loads(raw)
    except (BadSignature, SignatureExpired, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("cid"):
        return None
    if data.get("country") and data["country"] not in SUPPORTED_COUNTRIES:
        return None
    return data
