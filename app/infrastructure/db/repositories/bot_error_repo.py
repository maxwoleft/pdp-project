"""Lightweight async write для логування помилок AI чату."""
from __future__ import annotations

import logging
import traceback as _tb

from sqlalchemy import text

log = logging.getLogger("bot_error")


async def record_error(
    session_factory,
    *,
    source: str,
    exc: BaseException | None = None,
    error_message: str | None = None,
    chat_id: str | None = None,
    country: str | None = None,
    salon_id: str | None = None,
) -> None:
    """Insert error row. Безпечно — fail silently якщо БД недоступна."""
    try:
        err_type = type(exc).__name__ if exc else "Unknown"
        msg = error_message or (str(exc) if exc else "")
        tb = ""
        if exc is not None:
            try:
                tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-2000:]
            except Exception:
                pass
        async with session_factory() as s:
            await s.execute(text("""
                INSERT INTO public.bot_error
                  (chat_id, country, salon_id, source, error_type, error_message, traceback)
                VALUES (:cid, :c, :sid, :src, :et, :em, :tb)
            """), {
                "cid": chat_id, "c": country, "sid": salon_id,
                "src": source, "et": err_type[:120],
                "em": msg[:2000], "tb": tb,
            })
            await s.commit()
    except Exception as e:
        log.warning("bot_error persist fail: %s", e)
