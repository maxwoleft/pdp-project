"""Видаляє внутрішні (не-клієнтські) категорії + послуги з БД.

Виключаємо з AI-чату:
  - Add-on послуги (для співробітників)
  - Чайові
  - Ваучери, подарункові карти, абонементи (не послуги per se)

Логіка:
  1. Знаходимо top-level excluded categories по name patterns
  2. Рекурсивно — descendants по parent_id
  3. DELETE services з усього набору
  4. DELETE categories

Idempotent. Безпечно — bookings на excluded категоріях нема.

Запуск:
    python -m scripts.cleanup_addon_categories            # dry run
    python -m scripts.cleanup_addon_categories --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

COUNTRIES = ("ua", "pl", "gb")

# Шаблони для виключення (case-insensitive). Спрацьовує на category.name
EXCLUDED_PATTERNS = [
    "%add-on%", "%add on%", "%addon%",
    "%додатков%",          # "Додатково", "Додаткові"
    "%дополнительн%",      # "Дополнительные"
    "%чайов%",             # "Чайові", "ЧАЙОВІ"
    "%чаев%",              # "Чаевые"
    "%ваучер%",            # "Ваучер", "Ваучери"
    "%voucher%",
    "%подарунк%",          # "Подарункова карта"
    "%подарочн%",          # "Подарочный"
    "%gift card%",
    "%абонемент%",
    "%subscription%",
]


async def find_excluded_category_ids(session, country: str) -> set[str]:
    """Знаходить всі excluded category ids (recursive по parent)."""
    where_clauses = " OR ".join(f"name ILIKE :p{i}" for i in range(len(EXCLUDED_PATTERNS)))
    params = {f"p{i}": pat for i, pat in enumerate(EXCLUDED_PATTERNS)}
    rows = await session.execute(text(f"""
        WITH RECURSIVE excluded_tree AS (
            SELECT id FROM {country}.category
            WHERE archive = false AND ({where_clauses})
            UNION
            SELECT c.id FROM {country}.category c
            JOIN excluded_tree e ON c.parent_id = e.id
            WHERE c.archive = false
        )
        SELECT id FROM excluded_tree
    """), params)
    return {r[0] for r in rows.all()}


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            total_svc = 0
            total_cat = 0
            for c in COUNTRIES:
                excluded_ids = await find_excluded_category_ids(session, c)
                if not excluded_ids:
                    print(f"[{c}] excluded categories: 0")
                    continue

                svc_count = (await session.execute(text(f"""
                    SELECT COUNT(*) FROM {c}.service
                    WHERE category_id = ANY(:ids)
                """), {"ids": list(excluded_ids)})).scalar()

                print(f"[{c}] excluded categories: {len(excluded_ids)}, services: {svc_count}")
                total_svc += svc_count or 0
                total_cat += len(excluded_ids)

                if args.apply:
                    # 1. DELETE services
                    await session.execute(text(f"""
                        DELETE FROM {c}.service WHERE category_id = ANY(:ids)
                    """), {"ids": list(excluded_ids)})
                    # 2. DELETE categories: children first (no remaining FK from category.parent_id)
                    for _ in range(5):
                        deleted = (await session.execute(text(f"""
                            DELETE FROM {c}.category
                            WHERE id = ANY(:ids)
                              AND id NOT IN (SELECT parent_id FROM {c}.category WHERE parent_id IS NOT NULL)
                        """), {"ids": list(excluded_ids)})).rowcount
                        if not deleted:
                            break

            if args.apply:
                await session.commit()
                print(f"\nDONE: deleted {total_svc} services + {total_cat} categories")
            else:
                print(f"\nDRY RUN: would delete {total_svc} services + {total_cat} categories")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
