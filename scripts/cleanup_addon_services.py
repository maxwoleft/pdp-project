"""Видаляє add-on/modifier services за NAME pattern (не category).

Це доповнення до cleanup_addon_categories.py — там cleanup по category.name.
Тут — по service.name (бо modifier services часто живуть у звичайних категоріях).

Pattern маркери:
- "(додатково)", "(дод.)", "(доп.)"  — modifier marker
- "+до послуги"
- "орієнтовна вартість", "приблизна вартість"
- "по абонементу", "АБОНЕМЕНТ"
- Префікс "+ " (e.g. "+15 хв" — modifier time)

Idempotent. Безпечно — це pricing modifiers без власної booking-логіки.

Запуск:
    python -m scripts.cleanup_addon_services          # dry
    python -m scripts.cleanup_addon_services --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

COUNTRIES = ("ua", "pl", "gb")

NAME_PATTERNS_REGEX = (
    r"(\(додатково\)|\(дод\.?\)|\(доп\.?\)|\+до послуги|орієнтовн[ао] вартість|"
    r"приблизн[ао] вартість|по абонементу|АБОНЕМЕНТ|"
    r"^\s*\+ ?\d+\s*(хв|min|мин)|\(додатков|^\+ )"
)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    total_deleted = 0

    try:
        async with factory() as session:
            for c in COUNTRIES:
                preview = await session.execute(text(f"""
                    SELECT id, name FROM {c}.service
                    WHERE archive = false AND name ~* :pat
                    ORDER BY name LIMIT 10
                """), {"pat": NAME_PATTERNS_REGEX})
                preview_rows = preview.all()
                if not preview_rows:
                    print(f"[{c}] addon services: 0")
                    continue
                count_r = await session.execute(text(f"""
                    SELECT COUNT(*) FROM {c}.service
                    WHERE archive = false AND name ~* :pat
                """), {"pat": NAME_PATTERNS_REGEX})
                count = count_r.scalar()
                print(f"[{c}] addon services to delete: {count}")
                for r in preview_rows:
                    print(f"  • {r[1][:80]}")

                if args.apply:
                    d = await session.execute(text(f"""
                        DELETE FROM {c}.service
                        WHERE archive = false AND name ~* :pat
                    """), {"pat": NAME_PATTERNS_REGEX})
                    total_deleted += d.rowcount

            if args.apply:
                await session.commit()
                print(f"\nDONE: deleted {total_deleted} addon services")
            else:
                print("\nDRY RUN")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
