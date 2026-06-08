"""Merge service profiles: absorb one or more profiles into target keeper.

Профіль-keeper зберігає свій id/name/canonical_key. Поглинаються канонічні
ключі + key_descriptions з абсорбованих. Абсорбовані видаляються.

Запуск:
    python -m scripts.merge_profiles --country ua \\
        --keep "Борода та вуса" --absorb "БАРБЕР" "Інша назва"
    # Add --apply щоб commit. Без нього — dry run.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--keep", required=True, help="Назва profile-keeper (case-insensitive)")
    p.add_argument("--absorb", nargs="+", required=True, help="Назви абсорбованих profiles")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            keeper = (await session.execute(text(
                "SELECT id, name, canonical_key, canonical_keys, key_descriptions "
                "FROM public.service_profile "
                "WHERE country=:c AND LOWER(name)=LOWER(:n)"
            ), {"c": args.country, "n": args.keep})).first()
            if not keeper:
                print(f"ERROR: keeper '{args.keep}' not found in {args.country}")
                return

            absorb_rows = []
            for name in args.absorb:
                r = (await session.execute(text(
                    "SELECT id, name, canonical_keys, key_descriptions "
                    "FROM public.service_profile "
                    "WHERE country=:c AND LOWER(name)=LOWER(:n)"
                ), {"c": args.country, "n": name})).first()
                if not r:
                    print(f"ERROR: absorb target '{name}' not found in {args.country}")
                    return
                if str(r[0]) == str(keeper[0]):
                    print(f"ERROR: absorb '{name}' == keeper — skip self")
                    return
                absorb_rows.append(r)

            print(f"Keeper: '{keeper[1]}' (id={keeper[0]}, ckeys={len(keeper[3] or [])})")
            print(f"Absorb {len(absorb_rows)}:")
            for r in absorb_rows:
                print(f"  ← '{r[1]}' (id={r[0]}, ckeys={len(r[2] or [])})")

            # Merge
            merged_ckeys: set[str] = set(keeper[3] or [])
            merged_descs: dict[str, str] = dict(keeper[4] or {})
            for r in absorb_rows:
                merged_ckeys.update(r[2] or [])
                # Не overwriting існуючі описи keeper-а (його description more authoritative)
                for k, v in (r[3] or {}).items():
                    merged_descs.setdefault(k, v)
            merged_ckeys_list = sorted(merged_ckeys)
            print(f"\nMerged ckeys total: {len(merged_ckeys_list)} "
                  f"(was keeper={len(keeper[3] or [])})")

            if not args.apply:
                print("\nDRY RUN. Use --apply to commit.")
                return

            await session.execute(text("""
                UPDATE public.service_profile
                SET canonical_keys = CAST(:ck AS jsonb),
                    key_descriptions = CAST(:kd AS jsonb)
                WHERE id = :id
            """), {
                "ck": json.dumps(merged_ckeys_list),
                "kd": json.dumps(merged_descs),
                "id": str(keeper[0]),
            })
            for r in absorb_rows:
                await session.execute(text(
                    "DELETE FROM public.service_profile WHERE id = :id"
                ), {"id": str(r[0])})
            await session.commit()
            print(f"\nAPPLIED: {len(absorb_rows)} profile(s) absorbed into '{keeper[1]}'.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
