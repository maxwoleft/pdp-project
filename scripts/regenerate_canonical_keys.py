"""Регенерує canonical_key + brand/volume_ml/zones/session_minutes/ampules
на всіх services країни за поточною логікою app.domain.services.canonical_key.

Використовується після зміни нормалізації / synonym dict, щоб ckey у БД
стали узгодженими з новим алгоритмом.

Запуск:
    python -m scripts.regenerate_canonical_keys --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import text

from app.domain.services.canonical_key import extract_attributes
from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    country = args.country

    try:
        async with factory() as session:
            rows = (await session.execute(text(f"""
                SELECT id, name, canonical_key, brand, volume_ml, zones,
                       session_minutes, ampules
                FROM {country}.service
                WHERE archive=false
            """))).all()

        print(f"[{country}] services to process: {len(rows)}")

        changed_ckey = 0
        changed_attr = 0
        ckey_distribution_before: Counter[str] = Counter()
        ckey_distribution_after: Counter[str] = Counter()
        updates: list[dict] = []

        for r in rows:
            sid, name, old_ck, old_brand, old_vol, old_zones, old_min, old_amp = r
            ckey_distribution_before[old_ck or "<null>"] += 1
            attrs = extract_attributes(name or "")
            new_ck = attrs["base_name"] or None
            new_brand = attrs["brand"]
            new_vol = attrs["volume_ml"]
            new_zones = attrs["zones"]
            new_min = attrs["session_minutes"]
            new_amp = attrs["ampules"]
            ckey_distribution_after[new_ck or "<null>"] += 1

            ck_diff = (new_ck or "") != (old_ck or "")
            attr_diff = (
                (new_brand or "") != (old_brand or "")
                or new_vol != old_vol or new_zones != old_zones
                or new_min != old_min or new_amp != old_amp
            )
            if ck_diff:
                changed_ckey += 1
            if attr_diff:
                changed_attr += 1
            if ck_diff or attr_diff:
                updates.append({
                    "id": sid,
                    "ck": new_ck,
                    "br": new_brand,
                    "vol": new_vol,
                    "zn": new_zones,
                    "mn": new_min,
                    "amp": new_amp,
                })

        print(f"[{country}] services with ckey change: {changed_ckey}")
        print(f"[{country}] services with attr change: {changed_attr}")
        print(f"[{country}] distinct ckeys before: {len(ckey_distribution_before)}")
        print(f"[{country}] distinct ckeys after:  {len(ckey_distribution_after)}")
        delta = len(ckey_distribution_before) - len(ckey_distribution_after)
        print(f"[{country}] reduction: {delta} ckeys")

        if not args.apply:
            print("\nDRY RUN. Use --apply to commit.")
            return

        async with factory() as session:
            for idx, u in enumerate(updates, 1):
                await session.execute(text(f"""
                    UPDATE {country}.service SET
                      canonical_key = :ck,
                      brand = :br,
                      volume_ml = :vol,
                      zones = :zn,
                      session_minutes = :mn,
                      ampules = :amp
                    WHERE id = :id
                """), u)
                if idx % 500 == 0:
                    await session.commit()
                    print(f"  {idx}/{len(updates)} committed")
            await session.commit()
            print(f"\nAPPLIED: {len(updates)} services updated.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
