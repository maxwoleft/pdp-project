"""Оновлює expert-контент для service_profile + family option.

Читає .logs/profile_content/<CATEGORY>.json, знаходить profile по canonical_key
(з categorized.json) і UPDATE'ить translation + family option.

Запуск:
  python -m scripts.update_profile_content                  # dry run, всі
  python -m scripts.update_profile_content --apply
  python -m scripts.update_profile_content --apply --only UKLADKA,TONUVANNYA
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select, update

from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileOption,
    ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts._profile_expert_prompt import validate_response
from scripts.rebuild_profiles import make_profile_canonical_key, slugify_category

CATEGORIZED = Path(__file__).parent.parent / ".logs" / "canonical_keys_categorized.json"
CONTENT_DIR = Path(__file__).parent.parent / ".logs" / "profile_content"

LANGUAGE = "uk"


async def find_profile_for_category(session, category: str, items: list[dict]) -> ServiceProfile | None:
    """Знаходить profile, який було створено для категорії — по primary або synthetic key."""
    primary_key = make_profile_canonical_key(category, items)
    synthetic_key = slugify_category(category)
    p = (await session.execute(
        select(ServiceProfile).where(ServiceProfile.canonical_key == primary_key)
    )).scalar_one_or_none()
    if p:
        return p
    return (await session.execute(
        select(ServiceProfile).where(ServiceProfile.canonical_key == synthetic_key)
    )).scalar_one_or_none()


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only", help="Comma-separated categories")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None

    data = json.loads(CATEGORIZED.read_text(encoding="utf-8"))
    by_category = data["by_category"]

    engine = build_engine()
    factory = build_session_factory(engine)

    updated = 0
    skipped = 0
    not_found = 0
    warnings: list = []

    try:
        async with factory() as session:
            for category, items in by_category.items():
                if only and category not in only:
                    continue
                if category == "OTHER":
                    continue

                content_path = CONTENT_DIR / f"{category}.json"
                if not content_path.exists():
                    skipped += 1
                    continue

                content = json.loads(content_path.read_text(encoding="utf-8"))

                # Валідація на trigger words / типи / довжини
                errors = validate_response(content)
                if errors:
                    print(f"⚠ {category}: {errors}")
                    warnings.append((category, errors))

                profile = await find_profile_for_category(session, category, items)
                if not profile:
                    print(f"✖ {category}: profile not found")
                    not_found += 1
                    continue

                print(f"  {category:35s} → profile {profile.id[:8]} ({profile.canonical_key})")

                if not args.apply:
                    continue

                # Поля, які мають bad validation → не оновлювати
                bad_fields = {e.split(":")[0].strip() for e in errors if ":" in e}

                def safe(field, value):
                    if field in bad_fields:
                        return None
                    return value

                # UPDATE name профілю, якщо є новий
                if content.get("name") and content["name"] != profile.name:
                    await session.execute(
                        update(ServiceProfile)
                        .where(ServiceProfile.id == profile.id)
                        .values(name=content["name"], updated_by="expert_content")
                    )

                # UPDATE translation
                tr = (await session.execute(
                    select(ServiceProfileTranslation).where(
                        ServiceProfileTranslation.profile_id == profile.id,
                        ServiceProfileTranslation.language == LANGUAGE,
                    )
                )).scalar_one_or_none()
                if tr:
                    for fld in ("short_description", "addresses_problems", "target_audience",
                                "benefits", "keywords", "sales_pitch", "cross_sell",
                                "procedure_steps", "contraindications", "aftercare_advice"):
                        if fld in content:
                            val = safe(fld, content[fld])
                            if val is not None or fld == "aftercare_advice":
                                setattr(tr, fld, val)

                # UPDATE family option
                opt = (await session.execute(
                    select(ServiceProfileOption).where(
                        ServiceProfileOption.profile_id == profile.id,
                        ServiceProfileOption.option_type == "family",
                    )
                )).scalar_one_or_none()
                if opt:
                    if content.get("name"):
                        opt.name = content["name"]
                    for fld in ("short_description", "addresses_problems", "target_audience",
                                "benefits", "keywords", "sales_pitch", "cross_sell",
                                "procedure_steps", "contraindications", "aftercare_advice"):
                        if fld in content:
                            val = safe(fld, content[fld])
                            if val is not None or fld == "aftercare_advice":
                                setattr(opt, fld, val)

                # Brand-options (replace strategy)
                brand_opts = content.get("brand_options") or []
                if brand_opts:
                    # Видалити старі brand-options
                    existing_brand = list((await session.execute(
                        select(ServiceProfileOption).where(
                            ServiceProfileOption.profile_id == profile.id,
                            ServiceProfileOption.option_type == "brand",
                        )
                    )).scalars())
                    for o in existing_brand:
                        await session.delete(o)
                    await session.flush()

                    for i, b in enumerate(brand_opts, 1):
                        session.add(ServiceProfileOption(
                            profile_id=profile.id,
                            option_type="brand",
                            name=b["name"],
                            sort_order=i,
                            short_description=b.get("short_description", ""),
                            addresses_problems=b.get("addresses_problems", []),
                            target_audience=b.get("target_audience", []),
                            benefits=b.get("benefits", []),
                            keywords=b.get("keywords", []),
                            sales_pitch=b.get("sales_pitch"),
                            cross_sell=b.get("cross_sell", []),
                            procedure_steps=b.get("procedure_steps", []),
                            contraindications=b.get("contraindications", []),
                            aftercare_advice=b.get("aftercare_advice"),
                            canonical_keys=b.get("canonical_keys", []),
                        ))

                updated += 1

            if args.apply:
                await session.commit()
    finally:
        await engine.dispose()

    print(f"\n=== DONE ===")
    print(f"Updated: {updated}, Skipped (no content file): {skipped}, Not found: {not_found}")
    if warnings:
        print(f"Warnings: {len(warnings)}")


if __name__ == "__main__":
    asyncio.run(amain())
