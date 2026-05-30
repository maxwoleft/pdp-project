"""Перегенеровує key_descriptions для авто-присвоєних keys через rule-based extraction.
Зчитує лог-файли .logs/auto_link/{country}_*.json, перебудовує описи з нової логіки.
"""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import gen_key_description


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    log_dir = Path(".logs/auto_link")
    files = sorted(log_dir.glob(f"{args.country}_*.json"))
    if not files:
        print(f"No log files for {args.country}")
        return

    updates: list[tuple[str, str, str]] = []  # (profile_id, key, new_desc)
    for f in files:
        d = json.loads(f.read_text())
        for e in d.get("entries", []):
            if e.get("action") != "linked":
                continue
            new_desc = gen_key_description(e.get("sample_name", ""))
            if new_desc:
                updates.append((e["profile_id"], e["canonical_key"], new_desc))

    print(f"Updates to apply: {len(updates)}")
    for pid, k, desc in updates[:5]:
        print(f"  {k[:40]:40s} → {desc[:90]}")

    if not args.apply:
        print("DRY RUN")
        return

    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            for pid, k, desc in updates:
                await session.execute(text("""
                    UPDATE public.service_profile
                    SET key_descriptions = COALESCE(key_descriptions, '{}'::jsonb) || CAST(:dj AS jsonb)
                    WHERE id = :pid
                """), {"dj": json.dumps({k: desc}), "pid": pid})
            await session.commit()
            print(f"\nDONE: refreshed {len(updates)} key descriptions")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
