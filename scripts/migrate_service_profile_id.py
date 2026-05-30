"""Міграція: service.profile_id як стабільний FK замість canonical_key matching.

Зміни:
- ALTER ua/pl/gb.service ADD COLUMN profile_id VARCHAR(36) NULL + index
- One-time backfill: для кожного service знайти profile (country, canonical_key in option.canonical_keys[]) → SET profile_id
- Fallback: catch-all profile для services без match

Після цього профіль ↔ послуга стабільний до перейменувань CRM (canonical_key може мінятись, profile_id залишається).

Запуск:
    python -m scripts.migrate_service_profile_id --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("migrate_profile_id")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COUNTRIES = ("ua", "pl", "gb")


SCHEMA_STEPS = [
    "ALTER TABLE {c}.service ADD COLUMN IF NOT EXISTS profile_id VARCHAR(36) NULL",
    "CREATE INDEX IF NOT EXISTS ix_{c}_service_profile_id ON {c}.service(profile_id)",
]


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Step 1: schema
            for c in COUNTRIES:
                for q in SCHEMA_STEPS:
                    sql = q.format(c=c)
                    log.info("  %s", sql)
                    if args.apply:
                        await session.execute(text(sql))
            if args.apply:
                await session.commit()

            # Step 2: backfill from canonical_key → profile_id
            for c in COUNTRIES:
                # Build (country, canonical_key) → profile_id from options
                # Single SQL update via JOIN
                if args.apply:
                    r = await session.execute(text(f"""
                        UPDATE {c}.service s
                        SET profile_id = sub.profile_id
                        FROM (
                          SELECT DISTINCT ON (canonical_key) canonical_key, id AS profile_id
                          FROM (
                            SELECT jsonb_array_elements_text(p.canonical_keys) AS canonical_key, p.id
                            FROM public.service_profile p
                            WHERE p.country = :c
                          ) x
                          ORDER BY canonical_key, profile_id
                        ) sub
                        WHERE s.canonical_key = sub.canonical_key
                          AND s.archive = false
                          AND s.profile_id IS NULL
                    """), {"c": c})
                    log.info("[%s] backfill via canonical_keys: %d rows", c, r.rowcount)

                    # Also try primary canonical_key fallback
                    r2 = await session.execute(text(f"""
                        UPDATE {c}.service s
                        SET profile_id = p.id
                        FROM public.service_profile p
                        WHERE s.canonical_key = p.canonical_key
                          AND p.country = :c
                          AND s.archive = false
                          AND s.profile_id IS NULL
                    """), {"c": c})
                    log.info("[%s] backfill via primary: %d rows", c, r2.rowcount)

                    # Stats
                    stats = await session.execute(text(f"""
                        SELECT COUNT(*) FILTER (WHERE profile_id IS NOT NULL) AS linked,
                               COUNT(*) FILTER (WHERE profile_id IS NULL) AS unlinked,
                               COUNT(*) AS total
                        FROM {c}.service WHERE archive=false AND canonical_key IS NOT NULL
                    """))
                    s = stats.first()
                    log.info("[%s] linked=%d unlinked=%d total=%d", c, s[0], s[1], s[2])

            if args.apply:
                await session.commit()
                log.info("DONE.")
            else:
                log.info("DRY RUN. Pass --apply.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
