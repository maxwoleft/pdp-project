"""One-shot: заповнити GB Жіночі укладки + linkнути 128 services з Ladies Blow dry.

Запуск:
    python -m scripts.relink_gb_ukladka --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import gen_key_description

PROFILE_NAME = "Жіночі укладки"
COUNTRY = "gb"


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            pid_row = await session.execute(text(
                "SELECT id FROM public.service_profile "
                "WHERE country=:c AND name=:n"
            ), {"c": COUNTRY, "n": PROFILE_NAME})
            pid = pid_row.scalar()
            if not pid:
                raise RuntimeError(f"profile {PROFILE_NAME} ({COUNTRY}) not found")

            # Distinct (ckey, sample) у Ladies Blow dry без profile
            rows = (await session.execute(text(f"""
                SELECT s.canonical_key, MIN(s.name) AS sample, COUNT(*) AS cnt
                FROM {COUNTRY}.service s
                JOIN {COUNTRY}.category c ON c.id = s.category_id
                WHERE c.name ILIKE '%Ladies Blow%'
                  AND s.archive=false
                  AND s.profile_id IS NULL
                  AND s.canonical_key IS NOT NULL
                GROUP BY s.canonical_key
                ORDER BY cnt DESC
            """))).all()

            print(f"Found {len(rows)} distinct canonical_keys ({sum(r[2] for r in rows)} services)")

            ckeys = [r[0] for r in rows]
            descs = {r[0]: gen_key_description(r[1]) for r in rows}

            for ck, sample, cnt in rows:
                print(f"  + {ck:50s} | {cnt:3d} svc | {sample[:60]}")

            if args.apply:
                # Merge canonical_keys + key_descriptions
                await session.execute(text("""
                    UPDATE public.service_profile
                    SET canonical_keys = CAST(:keys AS jsonb),
                        key_descriptions = COALESCE(key_descriptions,'{}'::jsonb) || CAST(:desc AS jsonb)
                    WHERE id = :pid
                """), {
                    "keys": json.dumps(ckeys),
                    "desc": json.dumps(descs),
                    "pid": str(pid),
                })
                # Link services
                r = await session.execute(text(f"""
                    UPDATE {COUNTRY}.service SET profile_id = :pid
                    WHERE archive=false AND profile_id IS NULL
                      AND canonical_key = ANY(:keys)
                """), {"pid": str(pid), "keys": ckeys})
                await session.commit()
                print(f"\nAPPLIED: profile filled with {len(ckeys)} ckeys, services linked.")
            else:
                print("\nDRY RUN. Use --apply.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
