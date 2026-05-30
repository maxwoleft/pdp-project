"""Міграція ServiceProfile на per-country архітектуру.

Зміни:
- DROP UNIQUE constraint на canonical_key (глобальний)
- ADD UNIQUE (country, canonical_key)
- Зробити country NOT NULL
- ADD salon_ids JSONB DEFAULT '[]' (auto-computed по services у відповідній country schema)
- ADD cities JSONB DEFAULT '[]' (derived з salon_ids)

Дані:
- DELETE усі existing profiles (clean slate, per user request)

Запуск:
    python -m scripts.migrate_profiles_per_country --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


SQL_STEPS = [
    # 1. Видалити всі profiles (cascade видалить translations, options, variants, versions, overrides)
    "DELETE FROM public.service_profile",

    # 2. Drop existing UNIQUE constraint on canonical_key.
    # Назва constraint може бути різна. Пробуємо обидва типові варіанти.
    """
    DO $$
    DECLARE
        cn text;
    BEGIN
        SELECT conname INTO cn
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        JOIN pg_namespace n ON t.relnamespace = n.oid
        WHERE t.relname = 'service_profile' AND n.nspname = 'public'
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) ILIKE '%(canonical_key)%';
        IF cn IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.service_profile DROP CONSTRAINT %I', cn);
        END IF;
    END$$
    """,

    # Drop unique index якщо існує
    "DROP INDEX IF EXISTS public.service_profile_canonical_key_key",
    "DROP INDEX IF EXISTS public.ix_service_profile_canonical_key",

    # 3. Зробити country NOT NULL (вже видалені дані, тому без default)
    "ALTER TABLE public.service_profile ALTER COLUMN country SET NOT NULL",

    # 4. Додати composite unique
    """
    ALTER TABLE public.service_profile
    ADD CONSTRAINT uq_profile_country_canonical_key UNIQUE (country, canonical_key)
    """,

    # 5. Recreate index for canonical_key lookups (non-unique)
    "CREATE INDEX IF NOT EXISTS ix_service_profile_canonical_key ON public.service_profile(canonical_key)",
    "CREATE INDEX IF NOT EXISTS ix_service_profile_country_canonical ON public.service_profile(country, canonical_key)",

    # 6. salon_ids + cities
    "ALTER TABLE public.service_profile ADD COLUMN IF NOT EXISTS salon_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE public.service_profile ADD COLUMN IF NOT EXISTS cities JSONB NOT NULL DEFAULT '[]'::jsonb",
    "CREATE INDEX IF NOT EXISTS ix_service_profile_salon_ids ON public.service_profile USING GIN (salon_ids)",
    "CREATE INDEX IF NOT EXISTS ix_service_profile_cities ON public.service_profile USING GIN (cities)",
]


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            print("Pre-state:")
            r = await session.execute(text("SELECT COUNT(*) FROM public.service_profile"))
            print(f"  profiles: {r.scalar()}")
            r = await session.execute(text("SELECT COUNT(*) FROM public.service_profile_option"))
            print(f"  options: {r.scalar()}")

            if not args.apply:
                print("\nDRY RUN. Pass --apply to execute.")
                for i, q in enumerate(SQL_STEPS, 1):
                    short = " ".join(q.strip().split())[:120]
                    print(f"  {i}. {short}...")
                return

            for i, q in enumerate(SQL_STEPS, 1):
                short = " ".join(q.strip().split())[:80]
                print(f"  [{i}/{len(SQL_STEPS)}] {short}...")
                await session.execute(text(q))
                await session.flush()
            await session.commit()

            print("\nPost-state:")
            r = await session.execute(text("SELECT COUNT(*) FROM public.service_profile"))
            print(f"  profiles: {r.scalar()}")
            r = await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='service_profile' "
                "AND column_name IN ('country','salon_ids','cities') ORDER BY column_name"
            ))
            print(f"  new cols: {[row[0] for row in r.all()]}")
            print("\nDONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
