"""Верифікація канонічних ключів після синку.

Перевіряє:
1. Покриття: 0 послуг без canonical_key
2. Концентрація: total / unique ≥ 4
3. Топ-15 жирних груп — чи чисті
4. Token-order колізії (через групування за sorted-token-set)
5. Profile collisions (профілі з однаковим computed new_key)
6. Атрибути brand/volume/zones/session/ampules — coverage
"""
from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import select, text

from app.domain.services.canonical_key import extract_attributes
from app.infrastructure.db.models.profile import ServiceProfile
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

COUNTRIES = ("ua", "pl", "gb")


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        # 1. Coverage + concentration per country
        print("=== 1. Coverage + concentration ===")
        for c in COUNTRIES:
            async with country_session(factory, c) as session:
                stats = (await session.execute(text(f"""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE canonical_key IS NOT NULL) AS with_key,
                           COUNT(*) FILTER (WHERE canonical_key IS NULL) AS null_key,
                           COUNT(DISTINCT canonical_key) AS uniq_keys,
                           COUNT(*) FILTER (WHERE brand IS NOT NULL) AS with_brand,
                           COUNT(*) FILTER (WHERE volume_ml IS NOT NULL) AS with_vol,
                           COUNT(*) FILTER (WHERE zones IS NOT NULL) AS with_zones,
                           COUNT(*) FILTER (WHERE session_minutes IS NOT NULL) AS with_session,
                           COUNT(*) FILTER (WHERE ampules IS NOT NULL) AS with_amp
                    FROM {c}.service WHERE archive=false
                """))).first()
                concentration = stats.total / stats.uniq_keys if stats.uniq_keys else 0
                print(
                    f"[{c}] total={stats.total} with_key={stats.with_key} null={stats.null_key} "
                    f"uniq={stats.uniq_keys} concentration={concentration:.2f} | "
                    f"brand={stats.with_brand} vol={stats.with_vol} zones={stats.with_zones} "
                    f"session={stats.with_session} amp={stats.with_amp}"
                )

        # 2. Top heaviest groups across countries
        print("\n=== 2. Top-10 жирних груп (UA) ===")
        async with country_session(factory, "ua") as session:
            rows = (await session.execute(text("""
                SELECT canonical_key, COUNT(*) AS cnt, COUNT(DISTINCT name) AS uniq_names
                FROM ua.service WHERE archive=false AND canonical_key IS NOT NULL
                GROUP BY canonical_key
                ORDER BY cnt DESC LIMIT 10
            """))).all()
            for r in rows:
                print(f"  {r.canonical_key:50s} cnt={r.cnt:4d} unique_names={r.uniq_names}")

        # 3. Token-order — перевірити чи є канонічні ключі що відрізняються тільки порядком
        print("\n=== 3. Token-order колізії ===")
        async with country_session(factory, "ua") as session:
            rows = (await session.execute(text("""
                SELECT canonical_key FROM ua.service
                WHERE canonical_key IS NOT NULL AND archive=false
            """))).all()
        bytokens: dict[str, set[str]] = {}
        for r in rows:
            sorted_tokens = "_".join(sorted(r.canonical_key.split("_")))
            bytokens.setdefault(sorted_tokens, set()).add(r.canonical_key)
        collisions = {k: v for k, v in bytokens.items() if len(v) > 1}
        if collisions:
            print(f"  ⚠ ЗНАЙДЕНО {len(collisions)} token-order колізій:")
            for stoken, keys in list(collisions.items())[:5]:
                print(f"    sorted={stoken!r}  variants={list(keys)}")
        else:
            print("  ✓ 0 token-order колізій")

        # 4. Profile collisions
        print("\n=== 4. Profile collisions ===")
        async with factory() as session:
            profiles = list((await session.execute(select(ServiceProfile))).scalars())
            by_new_key: dict[str, list] = {}
            for p in profiles:
                if not p.name:
                    continue
                new_key = extract_attributes(p.name)["base_name"]
                if not new_key:
                    continue
                by_new_key.setdefault(new_key, []).append(p)
            collisions_p = {k: g for k, g in by_new_key.items() if len(g) > 1}
            if collisions_p:
                print(f"  ⚠ ЗНАЙДЕНО {len(collisions_p)} profile-колізій:")
                for k, group in list(collisions_p.items())[:5]:
                    names = [p.name for p in group]
                    print(f"    {k}: {names}")
            else:
                print("  ✓ 0 profile collisions")

        # 5. Profile linkage: скільки профілів зв'язані з реальними послугами
        print("\n=== 5. Profile linkage до послуг ===")
        async with factory() as session:
            profile_keys = {p.canonical_key for p in profiles if p.canonical_key}
            linked = 0
            total_services = 0
            for c in COUNTRIES:
                async with country_session(factory, c) as csession:
                    rows = (await csession.execute(text(f"""
                        SELECT DISTINCT canonical_key FROM {c}.service WHERE archive=false
                    """))).all()
                    keys = {r.canonical_key for r in rows if r.canonical_key}
                    matched = keys & profile_keys
                    total_services += len(keys)
                    linked += len(matched)
                    print(f"  [{c}] keys={len(keys)} matched_to_profile={len(matched)} "
                          f"coverage={len(matched)/len(keys)*100:.1f}%")
            print(f"  TOTAL: {linked}/{total_services} keys мають профіль "
                  f"({linked/total_services*100:.1f}%)")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
