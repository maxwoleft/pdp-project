"""Розрахунок повної KPI-аналітики для дашборда.

Усі метрики групуються по 5 секціях:
  A. Взаємодія з ботом
  B. Записи
  C. Клієнти
  D. Фінанси
  E. Якість бота
  F. Просунуте
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COUNTRIES = ("ua", "pl", "gb")


def _na() -> dict:
    return {"value": None, "label": "Збір не налаштований"}


async def _scalar(session: AsyncSession, sql: str, params: dict | None = None) -> int | float | None:
    r = await session.execute(text(sql), params or {})
    v = r.scalar()
    return v


async def _scalar_all_countries(session: AsyncSession, sql_template: str, params: dict | None = None) -> int | float:
    """Summarize a metric across 3 country schemas. SQL must use {country}.table."""
    total = 0
    for c in COUNTRIES:
        try:
            v = await _scalar(session, sql_template.replace("{country}", c), params)
            if v is not None:
                total += float(v)
        except Exception:
            pass
    return total


async def compute_metrics(session: AsyncSession, days: int = 30) -> dict:
    """Compute all KPIs. Period: last N days."""
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"start": period_start}

    # ── A. Взаємодія з ботом ──────────────────────────────────────────
    open_chats = await _scalar(session, """
        SELECT COUNT(*) FROM public.chat_dialog
        WHERE started_at >= :start
    """, params) or 0

    active_chats = await _scalar(session, """
        SELECT COUNT(*) FROM public.chat_dialog
        WHERE started_at >= :start AND turn_count > 2
    """, params) or 0

    # "Завершений" = >= 4 turns (2 exchanges) і last_message > 30min ago
    completed = await _scalar(session, """
        SELECT COUNT(*) FROM public.chat_dialog
        WHERE started_at >= :start
          AND turn_count >= 4
          AND last_message_at < (now() - interval '30 minutes')
    """, params) or 0

    total_dialogs = open_chats or 1
    completed_pct = round(100.0 * completed / total_dialogs, 1)

    # Кол-во діалогів з handoff на менеджера
    dialogs_with_mgr = await _scalar(session, """
        SELECT COUNT(DISTINCT chat_id) FROM public.manager_request
        WHERE created_at >= :start
    """, params) or 0
    solved_without_mgr_pct = round(100.0 * (total_dialogs - dialogs_with_mgr) / total_dialogs, 1)

    # Avg response time — потребує per-turn timestamps. Зараз chat_turn має created_at.
    avg_response_sec = await _scalar(session, """
        WITH pairs AS (
          SELECT
            chat_id,
            created_at AS user_at,
            LEAD(created_at) OVER (PARTITION BY chat_id ORDER BY seq) AS reply_at,
            role,
            LEAD(role) OVER (PARTITION BY chat_id ORDER BY seq) AS next_role
          FROM public.chat_turn
        )
        SELECT AVG(EXTRACT(EPOCH FROM (reply_at - user_at)))
        FROM pairs
        WHERE role = 'user' AND next_role = 'assistant' AND user_at >= :start
    """, params)
    avg_response_sec = round(float(avg_response_sec or 0), 1)

    # ── B. Записи ─────────────────────────────────────────────────────
    bookings_via_bot = 0
    bookings_via_admin = 0
    sum_check_bot = 0.0
    count_check_bot = 0
    sum_check_admin = 0.0
    count_check_admin = 0
    top_services: list[dict] = []
    bookings_per_client: dict[str, int] = {}
    repeat_bookings = 0
    new_clients = 0
    returning_clients = 0
    avg_gap_days = 0.0
    churn_clients = 0
    total_revenue = 0.0

    for c in COUNTRIES:
        try:
            bookings_via_bot += await _scalar(session, f"""
                SELECT COUNT(*) FROM {c}.booking
                WHERE created_at >= :start AND source_channel = 'ai_chat'
            """, params) or 0
            bookings_via_admin += await _scalar(session, f"""
                SELECT COUNT(*) FROM {c}.booking
                WHERE created_at >= :start AND (source_channel = 'admin' OR source_channel IS NULL)
            """, params) or 0

            # Sum + count для avg check (з JOIN service)
            rev_row = (await session.execute(text(f"""
                SELECT COALESCE(SUM(s.price), 0), COUNT(*)
                FROM {c}.booking b JOIN {c}.service s ON s.id = b.service_id
                WHERE b.created_at >= :start AND b.source_channel = 'ai_chat'
            """), params)).first()
            if rev_row:
                sum_check_bot += float(rev_row[0] or 0)
                count_check_bot += int(rev_row[1] or 0)
            total_revenue += float((rev_row[0] if rev_row else 0) or 0)

            adm_row = (await session.execute(text(f"""
                SELECT COALESCE(SUM(s.price), 0), COUNT(*)
                FROM {c}.booking b JOIN {c}.service s ON s.id = b.service_id
                WHERE b.created_at >= :start AND (b.source_channel = 'admin' OR b.source_channel IS NULL)
            """), params)).first()
            if adm_row:
                sum_check_admin += float(adm_row[0] or 0)
                count_check_admin += int(adm_row[1] or 0)

            # Top послуг
            top_rows = (await session.execute(text(f"""
                SELECT s.name, COUNT(*) AS cnt
                FROM {c}.booking b JOIN {c}.service s ON s.id = b.service_id
                WHERE b.created_at >= :start AND b.source_channel = 'ai_chat'
                GROUP BY s.name ORDER BY cnt DESC LIMIT 5
            """), params)).all()
            for r in top_rows:
                top_services.append({"name": r[0], "count": int(r[1]), "country": c})

            # Bookings per client
            client_rows = (await session.execute(text(f"""
                SELECT client_id, COUNT(*)
                FROM {c}.booking
                WHERE created_at >= :start AND source_channel = 'ai_chat'
                  AND client_id IS NOT NULL
                GROUP BY client_id
            """), params)).all()
            for r in client_rows:
                key = f"{c}:{r[0]}"
                bookings_per_client[key] = int(r[1])

            # New clients (first booking via ai_chat)
            new_clients += await _scalar(session, f"""
                SELECT COUNT(DISTINCT b.client_id) FROM {c}.booking b
                WHERE b.created_at >= :start AND b.source_channel = 'ai_chat'
                  AND NOT EXISTS (
                    SELECT 1 FROM {c}.booking b2
                    WHERE b2.client_id = b.client_id AND b2.created_at < b.created_at
                  )
            """, params) or 0

            # Returning clients (had old bookings, came back via ai_chat)
            returning_clients += await _scalar(session, f"""
                SELECT COUNT(DISTINCT b.client_id) FROM {c}.booking b
                WHERE b.created_at >= :start AND b.source_channel = 'ai_chat'
                  AND EXISTS (
                    SELECT 1 FROM {c}.booking b2
                    WHERE b2.client_id = b.client_id
                      AND b2.created_at < (now() - interval '60 days')
                  )
            """, params) or 0

            # Avg gap between bookings per client (retention proxy)
            gap_row = await _scalar(session, f"""
                WITH ordered AS (
                  SELECT client_id, created_at,
                         LAG(created_at) OVER (PARTITION BY client_id ORDER BY created_at) AS prev
                  FROM {c}.booking
                  WHERE source_channel = 'ai_chat' AND client_id IS NOT NULL
                )
                SELECT AVG(EXTRACT(EPOCH FROM (created_at - prev)) / 86400)
                FROM ordered WHERE prev IS NOT NULL
            """, params)
            if gap_row:
                avg_gap_days = max(avg_gap_days, float(gap_row or 0))

            # Churn — клієнти що не приходили > 60 днів
            churn_clients += await _scalar(session, f"""
                SELECT COUNT(DISTINCT client_id) FROM {c}.booking
                WHERE client_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM {c}.booking b2
                    WHERE b2.client_id = {c}.booking.client_id
                      AND b2.created_at >= (now() - interval '60 days')
                  )
            """, params) or 0
        except Exception:
            pass

    repeat_bookings = sum(1 for v in bookings_per_client.values() if v > 1)
    avg_bookings_per_client = round(
        sum(bookings_per_client.values()) / max(1, len(bookings_per_client)),
        2,
    )

    conversion_pct = round(100.0 * bookings_via_bot / max(1, active_chats), 1)

    avg_check_bot = round(sum_check_bot / max(1, count_check_bot), 2)
    avg_check_admin = round(sum_check_admin / max(1, count_check_admin), 2)

    # ── E. Якість бота ──────────────────────────────────────────────
    # Точність: dialogs зі ≥1 tool call / total
    dialogs_with_tools = await _scalar(session, """
        SELECT COUNT(DISTINCT chat_id) FROM public.chat_turn
        WHERE role = 'assistant' AND jsonb_array_length(tool_calls) > 0
          AND created_at >= :start
    """, params) or 0
    recognition_pct = round(100.0 * dialogs_with_tools / max(1, total_dialogs), 1)

    # Помилки: явно записані у bot_error + assistant turns з порожньою відповіддю
    tracked_errors = await _scalar(session, """
        SELECT COUNT(*) FROM public.bot_error
        WHERE created_at >= :start AND resolved = false
    """, params) or 0
    empty_replies = await _scalar(session, """
        SELECT COUNT(*) FROM public.chat_turn
        WHERE role = 'assistant'
          AND (content IS NULL OR LENGTH(TRIM(content)) = 0)
          AND created_at >= :start
    """, params) or 0
    errors_count = int(tracked_errors + empty_replies)

    handoff_pct = round(100.0 * dialogs_with_mgr / max(1, total_dialogs), 1)

    # ── F. Просунуте ──────────────────────────────────────────────
    # Pуковi години
    peak_rows = (await session.execute(text("""
        SELECT EXTRACT(HOUR FROM last_message_at)::int AS h, COUNT(*) AS cnt
        FROM public.chat_dialog WHERE last_message_at >= :start
        GROUP BY h ORDER BY cnt DESC LIMIT 5
    """), params)).all()
    peak_hours = [{"hour": int(r[0]), "count": int(r[1])} for r in peak_rows]

    # Сегментація клієнтів за частотою
    segments = {
        "Нові (1 запис)": sum(1 for v in bookings_per_client.values() if v == 1),
        "Повторні (2-3)": sum(1 for v in bookings_per_client.values() if 2 <= v <= 3),
        "Лояльні (4+)": sum(1 for v in bookings_per_client.values() if v >= 4),
    }

    # Channel breakdown (зараз тільки web)
    ch_rows = (await session.execute(text("""
        SELECT COALESCE(NULLIF(salon_id, ''), 'web') AS ch, COUNT(*)
        FROM public.chat_dialog WHERE started_at >= :start
        GROUP BY ch ORDER BY 2 DESC LIMIT 5
    """), params)).all()
    channels = [{"name": "web", "count": int(open_chats)}]  # placeholder, можна розширити коли додамо TG/IG

    return {
        "period_days": days,
        # A. Взаємодія
        "open_chats": int(open_chats),
        "active_chats": int(active_chats),
        "completed_pct": completed_pct,
        "avg_response_sec": avg_response_sec,
        "solved_without_mgr_pct": solved_without_mgr_pct,
        # B. Записи
        "bookings_via_bot": int(bookings_via_bot),
        "conversion_pct": conversion_pct,
        "avg_check_bot": avg_check_bot,
        "top_services": top_services[:5],
        # D. Фінанси
        "total_revenue": round(total_revenue, 2),
        "revenue_per_client": round(total_revenue / max(1, len(bookings_per_client)), 2),
        "avg_check_admin": avg_check_admin,
        "cac": None,  # потребує external cost data
        # E. Якість
        "recognition_pct": recognition_pct,
        "errors_count": int(errors_count),
        "handoff_pct": handoff_pct,
        # F. Просунуте
        "peak_hours": peak_hours,
        "channels": channels,
    }
