"""Shared helpers for syncing JSON files from AI Chat Postgres.

Used by categories.py / services.py / employees.py.
freetime.py не використовує — він тягне з AIHelps CRM напряму.

Постгрес-схема per-country: ua / pl / gb. Salon живе у відповідній схемі;
салон-таблиця має поля code, database_code, data_dir, status, location_slug,
sort_order (додані міграцією scripts/migrate_salon_booking_fields.py).

Усі ID у каталозі AI Chat зберігаються як f"{salon_id}:{crm_id}", де salon_id —
детермінований UUID. У JSON для booking ми повертаємо лише оригінальний crm_id.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

COUNTRIES = ("ua", "pl", "gb")
ACTIVE_STATUSES = ("active", "planned")


def strip_ns(prefixed_id: str | None, salon_id: str) -> str | None:
    """f'{salon_id}:{crm_id}' -> '{crm_id}'.

    Якщо префікс salon_id+':' відсутній — повертаємо як є (на випадок старих
    некоректно міграційованих даних).
    """
    if prefixed_id is None:
        return None
    pref = f"{salon_id}:"
    if prefixed_id.startswith(pref):
        return prefixed_id[len(pref):]
    return prefixed_id


def _dsn() -> str:
    load_dotenv()
    dsn = os.getenv("AI_CHAT_DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "AI_CHAT_DATABASE_URL is not set. Add it to booking_london_predict/.env "
            "(see .env.example for format)."
        )
    # psycopg2 не розуміє "postgresql+asyncpg://" — прибираємо суфікс драйвера.
    if dsn.startswith("postgresql+"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


@contextmanager
def chat_conn() -> Iterator[psycopg2.extensions.connection]:
    """Контекст-менеджер з autocommit-readonly підключенням до AI Chat Postgres."""
    conn = psycopg2.connect(_dsn())
    conn.set_session(readonly=True, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def load_salons(conn) -> list[dict]:
    """Список усіх активних/запланованих салонів (booking.salons — глобально).

    Повертає dict-и зі полями, потрібними скриптам:
      country, salon_id, code, database_code, data_dir, folder
    де folder = os.path.join("salons", data_dir).
    """
    salons: list[dict] = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, code, country, database_code, data_dir, location_slug, sort_order "
            "FROM booking.salons "
            "WHERE database_code IS NOT NULL "
            "AND data_dir IS NOT NULL "
            "AND status = ANY(%s) "
            "ORDER BY location_slug, sort_order",
            (list(ACTIVE_STATUSES),),
        )
        for r in cur.fetchall():
            salons.append({
                "country": r["country"],
                "salon_id": r["id"],
                "code": r["code"],
                "database_code": r["database_code"],
                "data_dir": r["data_dir"],
                "folder": os.path.join("salons", r["data_dir"]),
            })
    return salons


def output_path(salon: dict, filename: str) -> str:
    """Абсолютний шлях для JSON-файлу салону."""
    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "static", "data", salon["folder"],
    )
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, filename)
