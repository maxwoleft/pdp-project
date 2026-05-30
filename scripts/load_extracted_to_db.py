"""Завантажує витягнуті профілі з extracted_profiles.py у БД.

Використовує fuzzy matching для зіставлення назв з descServ.json
з canonical_keys у БД. Дані витягнуті Claude з реальних описів —
без OpenAI generation.

Запуск:
    python -m scripts.load_extracted_to_db              # dry run
    python -m scripts.load_extracted_to_db --apply      # створити/оновити
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from sqlalchemy import text as sql_text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.domain.services.canonical_key import (
    normalize_to_canonical_key,
    _ADDON_RE,
    _LENGTH_PATTERNS,
    _LEVEL_RE,
)
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.extracted_profiles import EXTRACTED_PROFILES
from scripts.seed_profiles_from_desc import fuzzy_match_db_key, clean_service_name


async def amain():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update", action="store_true", help="Оновити існуючі")
    args = parser.parse_args()

    print(f"Loaded {len(EXTRACTED_PROFILES)} extracted profiles")

    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    created = 0
    updated = 0
    skipped = 0
    no_match = 0

    try:
        async with factory() as session:
            repo = ServiceProfileRepository(session, embedder=embedder)

            # Зібрати всі canonical_keys з БД
            db_keys: set[str] = set()
            for c in ["ua", "pl", "gb"]:
                sql = sql_text(
                    f"SELECT DISTINCT canonical_key FROM {c}.service "
                    f"WHERE canonical_key IS NOT NULL AND archive = false"
                )
                for row in (await session.execute(sql)).fetchall():
                    db_keys.add(row[0])
            print(f"DB has {len(db_keys)} unique canonical_keys")

            total = len(EXTRACTED_PROFILES)
            for i, (service_name, fields) in enumerate(EXTRACTED_PROFILES.items(), 1):
                # Генеруємо canonical keys з різних варіантів назви
                variants = [v.strip() for v in service_name.split("\n") if v.strip()]
                all_keys: set[str] = set()
                for v in variants:
                    k = normalize_to_canonical_key(v)
                    if k:
                        all_keys.add(k)

                if not all_keys:
                    no_match += 1
                    continue

                # Fuzzy match
                matched_key = fuzzy_match_db_key(all_keys, db_keys)
                if not matched_key:
                    no_match += 1
                    continue

                svc_count = await repo.count_services_for_canonical_key(matched_key)
                if svc_count == 0:
                    no_match += 1
                    continue

                existing = await repo.get_by_canonical_key(matched_key)
                if existing and not args.update:
                    skipped += 1
                    continue

                clean_name = clean_service_name(variants[0])

                if not args.apply:
                    match_type = "exact" if matched_key in all_keys else "fuzzy"
                    has_p = "P" if fields.get("addresses_problems") else "."
                    has_b = "B" if fields.get("benefits") else "."
                    has_k = "K" if fields.get("keywords") else "."
                    print(f"  [{match_type:5s}] {has_p}{has_b}{has_k} {matched_key:50s} → {svc_count:4d} svc")
                    created += 1
                    continue

                # Підготовка даних — нормалізуємо типи
                short_desc = fields.get("short_description", "")[:500]
                if not short_desc:
                    short_desc = clean_name

                # aftercare_advice має бути str або None, не list
                aftercare = fields.get("aftercare_advice")
                if isinstance(aftercare, list):
                    aftercare = "; ".join(str(x) for x in aftercare) if aftercare else None
                elif not aftercare:
                    aftercare = None

                if existing:
                    await repo.upsert_translation(
                        existing.id,
                        "uk",
                        short_description=short_desc,
                        detailed_description=str(fields.get("detailed_description") or short_desc),
                        addresses_problems=fields.get("addresses_problems", []),
                        target_audience=fields.get("target_audience", []),
                        benefits=fields.get("benefits", []),
                        keywords=fields.get("keywords", []),
                        sales_pitch=fields.get("sales_pitch"),
                        cross_sell=fields.get("cross_sell", []),
                        procedure_steps=fields.get("procedure_steps", []),
                        contraindications=fields.get("contraindications", []),
                        aftercare_advice=aftercare,
                    )
                    await repo.update_fields(existing.id, name=clean_name, updated_by="extracted")
                    await repo.save_version(
                        existing.id,
                        change_summary="Updated from descServ.json (Claude extracted)",
                        created_by="extracted",
                    )
                    updated += 1
                    if i % 20 == 0:
                        print(f"  [{i}/{total}] ... {updated} updated, {created} created")
                else:
                    profile = await repo.create(
                        canonical_key=matched_key,
                        name=clean_name,
                        country=None,
                        default_language="uk",
                        enabled=True,
                        created_by="extracted",
                        updated_by="extracted",
                    )
                    await repo.upsert_translation(
                        profile.id,
                        "uk",
                        short_description=short_desc,
                        detailed_description=str(fields.get("detailed_description") or short_desc),
                        addresses_problems=fields.get("addresses_problems", []),
                        target_audience=fields.get("target_audience", []),
                        benefits=fields.get("benefits", []),
                        keywords=fields.get("keywords", []),
                        sales_pitch=fields.get("sales_pitch"),
                        cross_sell=fields.get("cross_sell", []),
                        procedure_steps=fields.get("procedure_steps", []),
                        contraindications=fields.get("contraindications", []),
                        aftercare_advice=aftercare,
                    )
                    await repo.save_version(
                        profile.id,
                        change_summary="Created from descServ.json (Claude extracted)",
                        created_by="extracted",
                    )
                    created += 1
                    if i % 20 == 0:
                        print(f"  [{i}/{total}] ... {updated} updated, {created} created")

            if args.apply:
                await session.commit()
                print(f"\nCommitted to DB")
    finally:
        await engine.dispose()

    print(f"\nDONE: {created} created, {updated} updated, {skipped} skipped, {no_match} no match")
    if not args.apply:
        print("(DRY RUN — use --apply to write to DB)")


if __name__ == "__main__":
    asyncio.run(amain())
