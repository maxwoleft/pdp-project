"""Backfill canonical_key + структурованих атрибутів для всіх послуг.

Що робить:
1. Для кожної послуги в ua/pl/gb.service:
   attrs = extract_attributes(name)
   UPDATE canonical_key, brand, volume_ml, zones, session_minutes, ampules
2. Будує мапу old_canonical_key → new_canonical_key (по модальному значенню)
3. Re-computes service_profile.canonical_key з profile.name
4. Re-maps service_profile_option.canonical_keys через мапу old→new

Idempotent. Перезапис керується тим, що extract_attributes — детермінована.

Запуск:
    python -m scripts.backfill_service_attributes              # dry run
    python -m scripts.backfill_service_attributes --apply      # реально оновити
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from decimal import Decimal

from sqlalchemy import select, update

from app.domain.services.canonical_key import extract_attributes, make_canonical_key
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.models.profile import ServiceProfile, ServiceProfileOption
from app.infrastructure.db.session import (
    build_engine,
    build_session_factory,
    country_session,
)

COUNTRIES = ("ua", "pl", "gb")


async def backfill_country(
    country: str, factory, apply: bool, mapping: dict[str, Counter]
) -> dict:
    stats = {"total": 0, "key_changed": 0, "brand_set": 0, "volume_set": 0,
             "zones_set": 0, "session_set": 0, "ampules_set": 0}

    async with country_session(factory, country) as session:
        services = list((await session.execute(select(Service))).scalars())
        stats["total"] = len(services)

        for s in services:
            attrs = extract_attributes(s.name or "")
            old_key = s.canonical_key
            new_key = make_canonical_key(attrs)

            # Будуємо мапу для подальшого update профілів
            if old_key and new_key and old_key != new_key:
                mapping.setdefault(old_key, Counter())[new_key] += 1
                stats["key_changed"] += 1

            if attrs["brand"]:
                stats["brand_set"] += 1
            if attrs["volume_ml"] is not None:
                stats["volume_set"] += 1
            if attrs["zones"] is not None:
                stats["zones_set"] += 1
            if attrs["session_minutes"] is not None:
                stats["session_set"] += 1
            if attrs["ampules"] is not None:
                stats["ampules_set"] += 1

            if apply:
                s.canonical_key = new_key
                s.brand = attrs["brand"]
                s.volume_ml = (
                    Decimal(str(attrs["volume_ml"])) if attrs["volume_ml"] is not None else None
                )
                s.zones = attrs["zones"]
                s.session_minutes = attrs["session_minutes"]
                s.ampules = attrs["ampules"]

        if apply:
            await session.commit()
    return stats


async def update_profiles(factory, mapping: dict[str, Counter], apply: bool) -> dict:
    """Оновлює service_profile.canonical_key (з profile.name) та
    service_profile_option.canonical_keys (через мапу).

    Колізії (декілька profiles → той самий new_key) НЕ оновлюються —
    логуються у `collisions`. Ці групи треба домерджити окремим скриптом
    (merge_duplicate_profiles.py), бо вони потребують ручного review.
    """
    flat_mapping: dict[str, str] = {
        old: new.most_common(1)[0][0] for old, new in mapping.items()
    }

    stats = {
        "profiles_updated": 0,
        "options_updated": 0,
        "options_keys_remapped": 0,
        "collisions": 0,
    }
    collisions: list[tuple[str, list[str]]] = []

    async with factory() as session:
        profiles = list((await session.execute(select(ServiceProfile))).scalars())

        # Згрупувати profiles по новому ключу (з profile.name)
        by_new_key: dict[str, list] = {}
        for p in profiles:
            if not p.name:
                continue
            new_key = extract_attributes(p.name)["base_name"]
            if not new_key:
                continue
            by_new_key.setdefault(new_key, []).append(p)

        # 1. ServiceProfile.canonical_key — оновлюємо тільки безконфліктні.
        # Алгоритм:
        #   a) Виділити профілі-кандидати на UPDATE (new_key != current AND in singleton group)
        #   b) Усі ІНШІ профілі лишаються зі своїми current canonical_key — їх ключі "frozen"
        #   c) Якщо кандидата new_key потрапляє в frozen — теж пропускаємо як collision
        #   d) Двофазний UPDATE через placeholder для transitive swap'ів між кандидатами

        candidate_ids: set[str] = set()
        candidate_targets: dict[str, str] = {}  # profile_id → new_key
        for new_key, group in by_new_key.items():
            if len(group) > 1:
                collisions.append((new_key, [p.canonical_key for p in group]))
                stats["collisions"] += 1
                continue
            p = group[0]
            if new_key == p.canonical_key:
                continue
            candidate_ids.add(p.id)
            candidate_targets[p.id] = new_key

        # frozen_keys = canonical_keys всіх профілів, що НЕ є кандидатами
        frozen_keys: set[str] = {
            p.canonical_key for p in profiles
            if p.canonical_key and p.id not in candidate_ids
        }

        # Відсіюємо кандидатів, чий target collides з frozen
        pending_updates: list[tuple[str, str]] = []
        for pid, new_key in candidate_targets.items():
            if new_key in frozen_keys:
                collisions.append((new_key, ["<candidate-vs-frozen>", pid]))
                stats["collisions"] += 1
                continue
            stats["profiles_updated"] += 1
            pending_updates.append((pid, new_key))

        if apply and pending_updates:
            # Phase 1: всі задіяні рядки → унікальний placeholder
            for pid, _ in pending_updates:
                await session.execute(
                    update(ServiceProfile)
                    .where(ServiceProfile.id == pid)
                    .values(canonical_key=f"__migrating_{pid}__")
                )
            await session.flush()
            # Phase 2: placeholder → реальний new_key
            for pid, new_key in pending_updates:
                await session.execute(
                    update(ServiceProfile)
                    .where(ServiceProfile.id == pid)
                    .values(canonical_key=new_key)
                )
            await session.flush()

        # 2. ServiceProfileOption.canonical_keys — мапа old→new
        options = list((await session.execute(select(ServiceProfileOption))).scalars())
        for opt in options:
            old_keys = list(opt.canonical_keys or [])
            if not old_keys:
                continue
            new_keys = []
            changed = False
            for k in old_keys:
                mapped = flat_mapping.get(k, k)
                if mapped != k:
                    changed = True
                if mapped not in new_keys:
                    new_keys.append(mapped)
            if changed:
                stats["options_updated"] += 1
                stats["options_keys_remapped"] += sum(
                    1 for k in old_keys if flat_mapping.get(k, k) != k
                )
                if apply:
                    opt.canonical_keys = new_keys

        if apply:
            await session.commit()

    if collisions:
        print(f"\n=== COLLISIONS ({len(collisions)}) — потребують ручного мерджу ===")
        for new_key, old_keys in collisions[:10]:
            print(f"  {new_key}  ←  {old_keys}")
        if len(collisions) > 10:
            print(f"  ... +{len(collisions) - 10} more")

    return stats


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Реально записати в БД")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    mapping: dict[str, Counter] = {}
    try:
        country_stats = {}
        for c in COUNTRIES:
            country_stats[c] = await backfill_country(c, factory, args.apply, mapping)
        profile_stats = await update_profiles(factory, mapping, args.apply)

        print("\n=== Service attributes ===")
        for c, s in country_stats.items():
            print(
                f"[{c}] total={s['total']} key_changed={s['key_changed']} "
                f"brand={s['brand_set']} volume_ml={s['volume_set']} "
                f"zones={s['zones_set']} session_min={s['session_set']} "
                f"ampules={s['ampules_set']}"
            )
        print(f"\n=== Mapping old→new keys: {len(mapping)} unique old keys remapped ===")
        print(f"\n=== Profiles ===")
        print(f"profiles_updated={profile_stats['profiles_updated']}")
        print(f"options_updated={profile_stats['options_updated']}")
        print(f"options_keys_remapped={profile_stats['options_keys_remapped']}")

        if not args.apply:
            print("\n(DRY RUN — use --apply to write changes)")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
