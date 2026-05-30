"""Обчислює salon_ids[] + cities[] для кожного ServiceProfile.

Логіка:
- Для кожного profile: collect усі canonical_keys з family + brand options.
- SELECT DISTINCT salon_id FROM {country}.service WHERE archive=false AND canonical_key = ANY(...)
- UPDATE profile.salon_ids = list, profile.cities = SELECT DISTINCT city FROM {country}.salon WHERE id = ANY(salon_ids)

Запуск:
    python -m scripts.compute_profile_salons --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    updated = 0

    try:
        async with factory() as session:
            rows = await session.execute(text("""
                SELECT p.id, p.country, p.name
                FROM public.service_profile p
                ORDER BY p.country, p.name
            """))
            profiles = rows.all()

            print(f"Profiles to process: {len(profiles)}")

            for pid, country, name in profiles:
                if country not in ("ua", "pl", "gb"):
                    continue

                # Стабільний lookup через service.profile_id (не canonical_keys)
                svc = await session.execute(text(f"""
                    SELECT DISTINCT salon_id FROM {country}.service
                    WHERE archive=false AND profile_id = :pid
                """), {"pid": str(pid)})
                salon_ids = sorted(r[0] for r in svc.all() if r[0])

                cities: list[str] = []
                if salon_ids:
                    cs = await session.execute(text(f"""
                        SELECT DISTINCT city FROM {country}.salon
                        WHERE id = ANY(:ids) AND city IS NOT NULL
                    """), {"ids": salon_ids})
                    cities = sorted(r[0] for r in cs.all() if r[0])

                print(f"  [{country}] {name:50s} salons={len(salon_ids):2d} cities={cities}")

                if args.apply:
                    await session.execute(text("""
                        UPDATE public.service_profile
                        SET salon_ids = CAST(:s AS jsonb),
                            cities    = CAST(:c AS jsonb)
                        WHERE id = :pid
                    """), {
                        "pid": pid,
                        "s": __import__("json").dumps(salon_ids),
                        "c": __import__("json").dumps(cities),
                    })
                    updated += 1

            if args.apply:
                await session.commit()
                print(f"\nDONE: updated {updated} profiles.")
            else:
                print("\nDRY RUN")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
