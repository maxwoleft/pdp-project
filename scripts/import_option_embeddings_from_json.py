"""Імпорт option.embedding з JSON. Match by (country, profile_name, option_type, option_name)."""
import argparse
import asyncio
import json

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    args = p.parse_args()

    with open(args.file) as f:
        rows = json.load(f)
    print(f"Loaded {len(rows)} rows")

    engine = build_engine()
    factory = build_session_factory(engine)
    matched = updated = skipped = 0

    try:
        async with factory() as session:
            for row in rows:
                if not row.get("embedding_str"):
                    skipped += 1
                    continue
                r = await session.execute(text("""
                    UPDATE public.service_profile_option o
                    SET embedding = CAST(:emb AS vector)
                    FROM public.service_profile p
                    WHERE o.profile_id = p.id
                      AND p.country = :c AND p.name = :pn
                      AND o.option_type = :ot AND o.name = :on
                """), {
                    "emb": row["embedding_str"],
                    "c": row["country"], "pn": row["profile_name"],
                    "ot": row["option_type"], "on": row["option_name"],
                })
                if r.rowcount:
                    updated += r.rowcount
                    matched += 1
                else:
                    skipped += 1
            await session.commit()
            print(f"DONE: matched={matched} updated={updated} skipped={skipped}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
