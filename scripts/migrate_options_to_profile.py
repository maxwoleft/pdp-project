"""Виносить canonical_keys, key_descriptions, keywords_by_lang з ServiceProfileOption
у ServiceProfile (родинний 1-shot migration).

Кроки:
1. ALTER service_profile ADD columns
2. Для кожного profile: aggregate з його options:
   - canonical_keys: UNION з family + brand options
   - key_descriptions: MERGE dict усіх options
   - keywords_by_lang: family option.keywords_by_lang (бренд не перетирає)
3. NOT drop options table — окремий step після перевірки

Запуск:
    python -m scripts.migrate_options_to_profile --apply
"""
import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("migrate_options")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


SCHEMA_STEPS = [
    "ALTER TABLE public.service_profile ADD COLUMN IF NOT EXISTS canonical_keys JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE public.service_profile ADD COLUMN IF NOT EXISTS key_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE public.service_profile ADD COLUMN IF NOT EXISTS keywords_by_lang JSONB NOT NULL DEFAULT '{}'::jsonb",
    "CREATE INDEX IF NOT EXISTS ix_service_profile_canonical_keys ON public.service_profile USING GIN (canonical_keys)",
]


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            for sql in SCHEMA_STEPS:
                log.info("  %s", sql)
                if args.apply:
                    await session.execute(text(sql))
            if args.apply:
                await session.commit()

            # Aggregate per profile
            rows = await session.execute(text("""
                SELECT p.id, p.canonical_key AS primary_key,
                       COALESCE(jsonb_agg(DISTINCT k) FILTER (WHERE k IS NOT NULL), '[]'::jsonb) AS all_keys,
                       COALESCE(jsonb_object_agg(kdk, kdv) FILTER (WHERE kdk IS NOT NULL), '{}'::jsonb) AS merged_descs,
                       (
                         SELECT keywords_by_lang FROM public.service_profile_option
                         WHERE profile_id = p.id AND option_type = 'family'
                         LIMIT 1
                       ) AS fam_keywords
                FROM public.service_profile p
                LEFT JOIN public.service_profile_option o ON o.profile_id = p.id
                LEFT JOIN LATERAL jsonb_array_elements_text(COALESCE(o.canonical_keys, '[]'::jsonb)) k ON TRUE
                LEFT JOIN LATERAL jsonb_each_text(COALESCE(o.key_descriptions, '{}'::jsonb)) kd(kdk, kdv) ON TRUE
                GROUP BY p.id, p.canonical_key
            """))

            count = 0
            for pid, primary_key, all_keys, merged_descs, fam_keywords in rows.all():
                # Add primary_key to canonical_keys if not already
                keys = list(all_keys or [])
                if primary_key and primary_key not in keys:
                    keys.insert(0, primary_key)
                descs = dict(merged_descs or {})
                kbl = dict(fam_keywords or {})

                if args.apply:
                    await session.execute(text("""
                        UPDATE public.service_profile
                        SET canonical_keys = CAST(:k AS jsonb),
                            key_descriptions = CAST(:d AS jsonb),
                            keywords_by_lang = CAST(:kb AS jsonb)
                        WHERE id = :pid
                    """), {
                        "pid": pid,
                        "k": json.dumps(keys),
                        "d": json.dumps(descs),
                        "kb": json.dumps(kbl),
                    })
                count += 1

            if args.apply:
                await session.commit()
                log.info("DONE: migrated %d profiles", count)
            else:
                log.info("DRY RUN. Would migrate %d profiles", count)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
