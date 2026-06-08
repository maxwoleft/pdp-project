"""Генерує employees.json для кожного активного салону.

Джерело: AI Chat Postgres (per-country schema), таблиця employee + M:N
employee_position з position.name.

Запуск:
  python employees.py
"""
from __future__ import annotations

import json

import psycopg2.extras

from _chat_db import chat_conn, load_salons, output_path, strip_ns

EXCLUDED_EMPLOYEE_NAME_KEYWORDS = ("співробітники",)


def _is_excluded(name: str | None) -> bool:
    n = (name or "").casefold()
    return any(k in n for k in EXCLUDED_EMPLOYEE_NAME_KEYWORDS)


def _fetch_employees(conn, country: str, salon_id: str) -> list[dict]:
    """Майстри салону у форматі, сумісному з AIHelps JSON.

    phone/email в JSON booking — масиви; у БД ми тримаємо JSONB-список, тож
    просто прокидаємо як є.
    """
    sql = f'''
        SELECT
            e.id AS id,
            e.name AS name,
            e.title AS title,
            e.phones AS phones,
            e.emails AS emails,
            e.roles AS roles,
            e.comments AS comments,
            e.photo AS photo,
            e.prepayment_required AS prepayment_required,
            e.archive AS archive,
            COALESCE(array_agg(ep.position_id ORDER BY ep.position_id)
                     FILTER (WHERE ep.position_id IS NOT NULL), '{{}}') AS position_ids,
            COALESCE(array_agg(p.name ORDER BY ep.position_id)
                     FILTER (WHERE p.name IS NOT NULL), '{{}}') AS position_names
        FROM "{country}".employee e
        LEFT JOIN "{country}".employee_position ep ON ep.employee_id = e.id
        LEFT JOIN "{country}".position p ON p.id = ep.position_id
        WHERE e.salon_id = %s AND e.archive = FALSE
        GROUP BY e.id
        ORDER BY e.name
    '''
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (salon_id,))
        rows = cur.fetchall()

    employees: list[dict] = []
    for r in rows:
        if _is_excluded(r["name"]):
            continue
        photo = r["photo"]
        employees.append({
            "id": strip_ns(r["id"], salon_id),
            "name": r["name"],
            "title": r["title"] or "",
            "phone": r["phones"] or [],
            "email": r["emails"] or [],
            "photo_exists": bool(photo),
            "photo": photo,
            "positions": [strip_ns(pid, salon_id) for pid in (r["position_ids"] or [])],
            "position_names": list(r["position_names"] or []),
            "roles": r["roles"] or [],
            "archive": bool(r["archive"]),
            "prepaymentRequired": bool(r["prepayment_required"]),
            "comments": r["comments"] or "",
            "commentsPlainText": r["comments"] or "",
        })
    return employees


def main() -> None:
    with chat_conn() as conn:
        salons = load_salons(conn)
        print(f"[employees] Found {len(salons)} active salons in AI Chat DB")

        for salon in salons:
            try:
                emps = _fetch_employees(conn, salon["country"], salon["salon_id"])
                path = output_path(salon, "employees.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(emps, f, ensure_ascii=False, indent=4)
                print(f"[employees] {salon['code']}: {len(emps)} -> {path}")
            except Exception as exc:  # noqa: BLE001
                print(f"[employees] {salon['code']}: ERROR - {exc}")


if __name__ == "__main__":
    main()
