"""Раw-SQL persistence для діалогів та turns. Lightweight, async.

Зберігає діалоги клієнтів з AI чатом + tool calls per assistant turn.
Викликається з conversation_service.process_web_turn.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_turn(
    session: AsyncSession,
    *,
    chat_id: str,
    country: str | None,
    salon_id: str | None,
    user_text: str,
    user_has_image: bool,
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
) -> None:
    """Upsert dialog + insert user turn + assistant turn."""
    # Upsert dialog
    await session.execute(text("""
        INSERT INTO public.chat_dialog (chat_id, country, salon_id, started_at, last_message_at, turn_count)
        VALUES (:cid, :c, :s, now(), now(), 2)
        ON CONFLICT (chat_id) DO UPDATE SET
          last_message_at = now(),
          turn_count = chat_dialog.turn_count + 2,
          salon_id = COALESCE(chat_dialog.salon_id, EXCLUDED.salon_id),
          country = COALESCE(chat_dialog.country, EXCLUDED.country)
    """), {"cid": chat_id, "c": country, "s": salon_id})

    # Determine next seq
    seq_row = await session.execute(text(
        "SELECT COALESCE(MAX(seq), 0) FROM public.chat_turn WHERE chat_id = :cid"
    ), {"cid": chat_id})
    seq = int(seq_row.scalar() or 0)

    # Insert user turn
    await session.execute(text("""
        INSERT INTO public.chat_turn (chat_id, seq, role, content, has_image, tool_calls)
        VALUES (:cid, :seq, 'user', :content, :has_img, '[]'::jsonb)
    """), {
        "cid": chat_id, "seq": seq + 1,
        "content": user_text, "has_img": user_has_image,
    })

    # Insert assistant turn (with tool calls)
    await session.execute(text("""
        INSERT INTO public.chat_turn (chat_id, seq, role, content, has_image, tool_calls)
        VALUES (:cid, :seq, 'assistant', :content, false, CAST(:tools AS jsonb))
    """), {
        "cid": chat_id, "seq": seq + 2,
        "content": assistant_text,
        "tools": json.dumps(tool_calls, ensure_ascii=False, default=str),
    })


async def list_dialogs(
    session: AsyncSession,
    *,
    country: str | None = None,
    salon_id: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Список діалогів з пагінацією."""
    where = []
    params: dict = {}
    if country:
        where.append("d.country = :country")
        params["country"] = country
    if salon_id:
        where.append("d.salon_id = :salon_id")
        params["salon_id"] = salon_id
    if search:
        where.append("EXISTS (SELECT 1 FROM public.chat_turn t WHERE t.chat_id = d.chat_id AND t.content ILIKE :search)")
        params["search"] = f"%{search}%"
    where_sql = " AND ".join(where) or "TRUE"

    total = (await session.execute(text(
        f"SELECT COUNT(*) FROM public.chat_dialog d WHERE {where_sql}"
    ), params)).scalar() or 0

    params["lim"] = page_size
    params["off"] = (page - 1) * page_size
    rows = (await session.execute(text(f"""
        SELECT d.chat_id, d.country, d.salon_id, d.started_at, d.last_message_at, d.turn_count,
               (SELECT content FROM public.chat_turn WHERE chat_id = d.chat_id AND role = 'user'
                ORDER BY seq ASC LIMIT 1) AS first_user_msg
        FROM public.chat_dialog d
        WHERE {where_sql}
        ORDER BY d.last_message_at DESC
        LIMIT :lim OFFSET :off
    """), params)).all()

    items = [
        {
            "chat_id": r[0], "country": r[1], "salon_id": r[2],
            "started_at": r[3], "last_message_at": r[4],
            "turn_count": r[5], "first_user_msg": r[6] or "",
        }
        for r in rows
    ]
    return {
        "items": items, "total": total,
        "page": page, "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


async def get_dialog(session: AsyncSession, chat_id: str) -> dict | None:
    """Деталі діалогу — metadata + всі turns з tool calls."""
    d_row = (await session.execute(text("""
        SELECT chat_id, country, salon_id, started_at, last_message_at, turn_count
        FROM public.chat_dialog WHERE chat_id = :cid
    """), {"cid": chat_id})).first()
    if not d_row:
        return None
    turns_rows = (await session.execute(text("""
        SELECT seq, role, content, has_image, tool_calls, created_at
        FROM public.chat_turn WHERE chat_id = :cid ORDER BY seq ASC
    """), {"cid": chat_id})).all()
    turns = [
        {
            "seq": t[0], "role": t[1], "content": t[2],
            "has_image": t[3], "tool_calls": t[4] or [],
            "created_at": t[5],
        }
        for t in turns_rows
    ]
    return {
        "chat_id": d_row[0], "country": d_row[1], "salon_id": d_row[2],
        "started_at": d_row[3], "last_message_at": d_row[4],
        "turn_count": d_row[5], "turns": turns,
    }
