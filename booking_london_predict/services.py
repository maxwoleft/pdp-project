"""Генерує services.json для кожного активного салону.

Джерело: AI Chat Postgres (per-country schema), таблиця service з join до
category (поточної + parent) і position.

Запуск:
  python services.py
"""
from __future__ import annotations

import json

import psycopg2.extras

from _chat_db import chat_conn, load_salons, output_path, strip_ns


def _fetch_services(conn, country: str, salon_id: str) -> list[dict]:
    """Послуги салону у форматі, сумісному з historic AIHelps JSON.

    location_prices у нашому JSON — це число (старий формат),
    location_position — raw CRM id позиції.
    """
    sql = f'''
        SELECT
            s.id AS id,
            s.name AS name,
            s.description_plain AS description,
            s.duration_min AS duration,
            s.price AS price,
            s.price_currency AS price_currency,
            s.archive AS archive,
            s.position_id AS position_id,
            c.name AS category_name,
            p.name AS parent_name
        FROM "{country}".service s
        JOIN "{country}".category c ON c.id = s.category_id
        LEFT JOIN "{country}".category p ON p.id = c.parent_id
        WHERE s.salon_id = %s AND s.archive = FALSE
        ORDER BY c.name, s.name
    '''
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (salon_id,))
        rows = cur.fetchall()

    services: list[dict] = []
    for r in rows:
        services.append({
            "id": strip_ns(r["id"], salon_id),
            "name": r["name"],
            "description": r["description"] or "",
            "duration": int(r["duration"] or 0),
            "price_currency": r["price_currency"],
            "category": r["category_name"],
            "location_prices": float(r["price"]) if r["price"] is not None else None,
            "archive": bool(r["archive"]),
            "location_position": strip_ns(r["position_id"], salon_id),
            "parent": r["parent_name"],
        })
    return services


def main() -> None:
    with chat_conn() as conn:
        salons = load_salons(conn)
        print(f"[services] Found {len(salons)} active salons in AI Chat DB")

        for salon in salons:
            try:
                svc = _fetch_services(conn, salon["country"], salon["salon_id"])
                path = output_path(salon, "services.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(svc, f, ensure_ascii=False, indent=4)
                print(f"[services] {salon['code']}: {len(svc)} -> {path}")
            except Exception as exc:  # noqa: BLE001
                print(f"[services] {salon['code']}: ERROR - {exc}")


if __name__ == "__main__":
    main()
