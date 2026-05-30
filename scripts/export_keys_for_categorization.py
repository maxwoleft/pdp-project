"""Витягує всі унікальні canonical_keys + sample service.name для категоризації.

Output: .logs/canonical_keys_analysis.json
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

COUNTRIES = ("ua", "pl", "gb")
OUTPUT = Path(__file__).parent.parent / ".logs" / "canonical_keys_analysis.json"


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            # Збираємо ключі + sample names + counts + brands
            data: dict = {}
            for c in COUNTRIES:
                rows = await session.execute(text(f"""
                    SELECT
                        canonical_key,
                        COUNT(*) AS svc_count,
                        ARRAY_AGG(DISTINCT name ORDER BY name) FILTER (WHERE name IS NOT NULL) AS sample_names,
                        ARRAY_AGG(DISTINCT brand ORDER BY brand) FILTER (WHERE brand IS NOT NULL) AS brands
                    FROM {c}.service
                    WHERE archive = false AND canonical_key IS NOT NULL
                    GROUP BY canonical_key
                """))
                for r in rows.all():
                    k = r.canonical_key
                    if k not in data:
                        data[k] = {
                            "canonical_key": k,
                            "svc_count": 0,
                            "names": set(),
                            "brands": set(),
                            "countries": set(),
                        }
                    data[k]["svc_count"] += r.svc_count
                    data[k]["names"].update(r.sample_names or [])
                    data[k]["brands"].update(r.brands or [])
                    data[k]["countries"].add(c)

            # Сортуємо за coverage, потім за key
            result = sorted(
                [
                    {
                        "canonical_key": v["canonical_key"],
                        "svc_count": v["svc_count"],
                        "names": sorted(list(v["names"]))[:5],  # top 5 unique names
                        "brands": sorted(list(v["brands"])),
                        "countries": sorted(list(v["countries"])),
                    }
                    for v in data.values()
                ],
                key=lambda x: (-x["svc_count"], x["canonical_key"]),
            )

            OUTPUT.parent.mkdir(exist_ok=True)
            OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"Saved {len(result)} canonical_keys to {OUTPUT}")

            # Підсумок
            print(f"\n=== Підсумок ===")
            print(f"Total unique canonical_keys: {len(result)}")
            print(f"Total services covered:      {sum(r['svc_count'] for r in result)}")
            print(f"Keys with brand:             {sum(1 for r in result if r['brands'])}")
            print(f"Keys with ≥2 brands:         {sum(1 for r in result if len(r['brands']) >= 2)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
