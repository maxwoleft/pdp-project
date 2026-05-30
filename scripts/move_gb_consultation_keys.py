"""GB: переносить first-session keys з 'Інші консультації' у правильні profile."""
import asyncio
import json

from sqlalchemy import text
from app.infrastructure.db.session import build_engine, build_session_factory


MOVES = {
    "Stratosphere": ["pershyi_seans_stratosfery"],
    "Масаж": [
        "antytselyulitnyi_masazh_pershyi",
        "limfodrenazhnyi_masazh_pershyi",
        "masazh_pershyi",
        "masazh_pershyi_relaks",
        "masazh_pershyi_sportyvnyi",
    ],
    "Icoone Laser": ["lazer_pershyi_seans"],
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            all_keys: list[str] = []
            for target_name, keys in MOVES.items():
                r = await session.execute(text(
                    "SELECT id, canonical_keys FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": target_name})
                row = r.first()
                if not row:
                    print(f"  ⚠ {target_name} GB not found — skip")
                    continue
                existing = list(row[1] or [])
                merged = list(dict.fromkeys(existing + keys))
                await session.execute(text(
                    "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                ), {"k": json.dumps(merged), "id": row[0]})
                print(f"  + {target_name}: {len(existing)} → {len(merged)} keys")
                all_keys.extend(keys)

            # Remove from "Інші консультації" GB
            r = await session.execute(text(
                "SELECT id, canonical_keys FROM service_profile WHERE country='gb' AND name='Інші консультації'"
            ))
            row = r.first()
            if row:
                cleaned = [k for k in (row[1] or []) if k not in all_keys]
                await session.execute(text(
                    "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                ), {"k": json.dumps(cleaned), "id": row[0]})
                print(f"  − removed {len(row[1] or []) - len(cleaned)} keys з 'Інші консультації' GB")

            await session.commit()

            # Re-link
            await session.execute(text(
                "UPDATE gb.service SET profile_id = NULL WHERE archive=false AND canonical_key = ANY(:k)"
            ), {"k": all_keys})
            r = await session.execute(text("""
                UPDATE gb.service s SET profile_id = sub.profile_id
                FROM (
                  SELECT DISTINCT ON (canonical_key) canonical_key, id AS profile_id
                  FROM (
                    SELECT jsonb_array_elements_text(p.canonical_keys) AS canonical_key, p.id
                    FROM public.service_profile p WHERE p.country='gb'
                  ) x ORDER BY canonical_key, profile_id
                ) sub
                WHERE s.canonical_key = sub.canonical_key AND s.archive=false AND s.profile_id IS NULL
            """))
            print(f"  ↻ gb re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
