"""Генерує categories.json для кожного активного салону.

Джерело даних: AI Chat Postgres (схема per country: ua/pl/gb), таблиця category.
Раніше скрипт ходив у AIHelps CRM напряму — тепер каталог тримає AI Chat,
а тут лише експорт у JSON для booking frontend.

Запуск:
  python categories.py

Залежності: psycopg2-binary, python-dotenv. AI_CHAT_DATABASE_URL у .env.
"""
from __future__ import annotations

import json

import psycopg2.extras

from _chat_db import chat_conn, load_salons, output_path, strip_ns


def _fetch_categories(conn, country: str, salon_id: str) -> list[dict]:
    """Тягне категорії одного салону у форматі, сумісному з historic AIHelps JSON."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f'SELECT id, name, parent_id, picture, archive '
            f'FROM "{country}".category '
            f"WHERE salon_id = %s AND archive = FALSE "
            f"ORDER BY name",
            (salon_id,),
        )
        rows = cur.fetchall()

    # name lookup для parentName (по ns-id)
    name_by_id = {r["id"]: r["name"] for r in rows}

    categories: list[dict] = []
    for r in rows:
        categories.append({
            "id": strip_ns(r["id"], salon_id),
            "name": r["name"],
            "parent": strip_ns(r["parent_id"], salon_id),
            "picture": r["picture"],
            "archive": bool(r["archive"]),
            "parentName": name_by_id.get(r["parent_id"]),
        })
    return categories


def main() -> None:
    with chat_conn() as conn:
        salons = load_salons(conn)
        print(f"[categories] Found {len(salons)} active salons in AI Chat DB")

        for salon in salons:
            try:
                cats = _fetch_categories(conn, salon["country"], salon["salon_id"])
                path = output_path(salon, "categories.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cats, f, ensure_ascii=False, indent=4)
                print(f"[categories] {salon['code']}: {len(cats)} -> {path}")
            except Exception as exc:  # noqa: BLE001
                print(f"[categories] {salon['code']}: ERROR - {exc}")


if __name__ == "__main__":
    main()
