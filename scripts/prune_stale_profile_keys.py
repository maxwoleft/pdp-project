"""Очищає stale canonical_keys у profile options + primary key.

Логіка:
- Будує all_real set per country (DISTINCT canonical_key з {country}.service WHERE archive=false)
- Для кожного profile:
  - Прибирає stale keys з option.canonical_keys[]
  - Якщо primary profile.canonical_key не валідний — замінює на перший valid з family option
    (через placeholder swap для уникнення UNIQUE collision)
  - Якщо в усіх options 0 valid keys → DELETE profile

Запуск:
    python -m scripts.prune_stale_profile_keys --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption,
)
from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    deleted = 0
    cleaned = 0
    swapped_primary = 0

    try:
        async with factory() as session:
            real_by_country: dict[str, set[str]] = {}
            for c in ("ua", "pl", "gb"):
                r = await session.execute(text(
                    f"SELECT DISTINCT canonical_key FROM {c}.service "
                    "WHERE archive=false AND canonical_key IS NOT NULL"
                ))
                real_by_country[c] = {row[0] for row in r.all() if row[0]}

            profiles = list((await session.execute(
                select(ServiceProfile)
            )).scalars().unique().all())

            for p in profiles:
                real = real_by_country.get(p.country, set())
                # Clean options
                options = list((await session.execute(
                    select(ServiceProfileOption).where(ServiceProfileOption.profile_id == p.id)
                )).scalars().all())

                total_valid_keys = 0
                family_valid_first: str | None = None
                for opt in options:
                    cur = list(opt.canonical_keys or [])
                    valid = [k for k in cur if k in real]
                    if len(valid) != len(cur):
                        if args.apply:
                            opt.canonical_keys = valid
                            opt.embedding = None
                        cleaned += 1
                        print(f"  [{p.country}] {p.name:45s} option={opt.name:30s} {len(cur)} → {len(valid)} keys")
                    if opt.option_type == "family" and family_valid_first is None and valid:
                        family_valid_first = valid[0]
                    total_valid_keys += len(valid)

                if total_valid_keys == 0:
                    print(f"  ✖ DELETE profile [{p.country}] {p.name} (0 valid keys)")
                    if args.apply:
                        await session.delete(p)
                    deleted += 1
                    continue

                # Fix primary canonical_key якщо stale
                if p.canonical_key not in real and family_valid_first:
                    # Перевірити чи family_valid_first ще не зайнятий іншим profile тієї ж country
                    occupy = await session.execute(text(
                        "SELECT id FROM public.service_profile "
                        "WHERE country = :c AND canonical_key = :k AND id != :pid"
                    ), {"c": p.country, "k": family_valid_first, "pid": p.id})
                    if occupy.scalar():
                        print(f"  ⚠ [{p.country}] {p.name}: target {family_valid_first} зайнятий іншим profile — skip swap")
                        continue
                    if args.apply:
                        # Two-phase swap
                        placeholder = f"__tmp__{uuid.uuid4().hex[:10]}"
                        await session.execute(text(
                            "UPDATE public.service_profile SET canonical_key = :p WHERE id = :pid"
                        ), {"p": placeholder, "pid": p.id})
                        await session.flush()
                        await session.execute(text(
                            "UPDATE public.service_profile SET canonical_key = :k WHERE id = :pid"
                        ), {"k": family_valid_first, "pid": p.id})
                        await session.flush()
                    print(f"  ↻ [{p.country}] {p.name}: primary {p.canonical_key} → {family_valid_first}")
                    swapped_primary += 1

            if args.apply:
                await session.commit()

            print(f"\nSummary: deleted={deleted}, cleaned_options={cleaned}, swapped_primary={swapped_primary}")
            if not args.apply:
                print("DRY RUN. Pass --apply.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
