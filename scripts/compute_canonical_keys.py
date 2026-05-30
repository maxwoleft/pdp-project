"""Обчислює canonical_key для всіх послуг у БД.

Один раз робиться для існуючого каталогу. Потім — при sync_from_crm
для нових/оновлених послуг.

Запуск:
    python -m scripts.compute_canonical_keys
    python -m scripts.compute_canonical_keys --country ua
    python -m scripts.compute_canonical_keys --dry-run    # тільки показати статистику
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import select, update

from app.domain.services.canonical_key import normalize_to_canonical_key
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.session import build_engine, build_session_factory, country_session


async def process_country(country: str, factory, dry_run: bool) -> dict:
    async with country_session(factory, country) as session:
        services = (await session.execute(select(Service))).scalars().all()
        services = list(services)

    print(f"\n[{country}] {len(services)} services total")

    # Обчислюємо ключі (use UA name first if available, else original name)
    keys: list[tuple[str, str]] = []  # (id, key)
    for svc in services:
        # Беремо найкращу мовну версію назви для ключа
        source = svc.name_uk or svc.name
        key = normalize_to_canonical_key(source)
        keys.append((svc.id, key))

    # Статистика
    counter = Counter(k for _, k in keys if k)
    empty = sum(1 for _, k in keys if not k)
    unique = len(counter)
    avg_per_key = sum(counter.values()) / unique if unique else 0

    print(f"[{country}] unique canonical_keys: {unique}")
    print(f"[{country}] avg services per key: {avg_per_key:.1f}")
    print(f"[{country}] empty keys: {empty}")
    print(f"[{country}] top 10 most populated keys:")
    for k, n in counter.most_common(10):
        print(f"  {n:5d}  {k}")

    if dry_run:
        return {"total": len(services), "unique": unique, "empty": empty}

    # Оновлюємо в БД (батчами)
    async with country_session(factory, country) as session:
        BATCH = 200
        for i in range(0, len(keys), BATCH):
            batch = keys[i : i + BATCH]
            for svc_id, key in batch:
                await session.execute(
                    update(Service).where(Service.id == svc_id).values(canonical_key=key or None)
                )

    print(f"[{country}] done — {len(keys)} services updated")
    return {"total": len(services), "unique": unique, "empty": empty}


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=["ua", "pl", "gb"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    countries = [args.country] if args.country else ["ua", "pl", "gb"]
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        for c in countries:
            await process_country(c, factory, args.dry_run)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
