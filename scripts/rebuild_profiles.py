"""Rebuild всіх service_profile + options з нуля на основі categorized.json.

Логіка:
1. Читає .logs/canonical_keys_categorized.json (категоризований аналіз 3104 keys)
2. Для кожної категорії з ≥10 послуг → створює ServiceProfile + UK translation
3. profile.canonical_key = представник категорії (найпопулярніший)
4. ServiceProfileOption створюється для кожної категорії з canonical_keys=[список всіх keys у категорії]
5. Окрім того, де є ≥2 брендів → додатково створюються brand-options
6. Контент для кожної категорії — з .logs/profiles_content_<category>.json (override)
   або з вбудованих placeholder'ів якщо файлу немає.

Запуск:
  python -m scripts.rebuild_profiles                # dry run
  python -m scripts.rebuild_profiles --apply
  python -m scripts.rebuild_profiles --apply --only "UKLADKA,TONUVANNYA"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileOption,
    ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory

CATEGORIZED = Path(__file__).parent.parent / ".logs" / "canonical_keys_categorized.json"
CONTENT_DIR = Path(__file__).parent.parent / ".logs" / "profile_content"

MIN_SVC_COUNT = 1  # 100% покриття: створюємо профіль навіть для категорії з 1 послугою
LANGUAGE = "uk"


def slugify_category(cat: str) -> str:
    """OFFICIAL_NAME → official_name (для canonical_key)."""
    return f"fam_{cat.lower()}"


def make_profile_canonical_key(category: str, items: list[dict]) -> str:
    """canonical_key для profile = найбільший за coverage key категорії.

    Якщо неможливо (немає унікального), використовуємо synthetic fam_X."""
    if not items:
        return slugify_category(category)
    # Спробуємо найкращий ключ (max svc_count)
    top = max(items, key=lambda x: x["svc_count"])
    return top["canonical_key"]


def load_content(category: str) -> dict | None:
    """Підвантажує вручну написаний контент для категорії."""
    fp = CONTENT_DIR / f"{category}.json"
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return None


def placeholder_content(category: str, name: str, svc_count: int, sample_names: list[str]) -> dict:
    """Тимчасовий placeholder для категорій без manual content."""
    return {
        "name": name,
        "short_description": (
            f"Послуги категорії «{name}» доступні в наших салонах. "
            f"Майстер підбирає конкретний формат відповідно до вашої потреби."
        ),
        "addresses_problems": [
            "потрібна професійна процедура",
            "хочеться доглянутого вигляду",
        ],
        "target_audience": ["клієнти, які підтримують доглянутий образ"],
        "benefits": [
            "професійне виконання у руках досвідченого майстра",
            "індивідуальний підбір формату під ваш тип",
        ],
        "keywords": [name.lower()] + [n.lower() for n in sample_names[:3]],
        "sales_pitch": f"Раджу запис на консультацію — майстер оцінить ваш стан і підбере оптимальний формат.",
        "cross_sell": [],
        "procedure_steps": [],
        "contraindications": [],
        "aftercare_advice": None,
    }


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only", help="Comma-separated categories to process")
    parser.add_argument("--min-svc", type=int, default=MIN_SVC_COUNT,
                        help="Мінімальна к-ть послуг у категорії для створення profile")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None

    data = json.loads(CATEGORIZED.read_text(encoding="utf-8"))
    by_category = data["by_category"]

    engine = build_engine()
    factory = build_session_factory(engine)

    stats = {"created": 0, "with_content": 0, "with_placeholder": 0,
             "options_created": 0, "categories_skipped": 0}

    try:
        async with factory() as session:
            existing_keys = {
                k[0] for k in (await session.execute(
                    select(ServiceProfile.canonical_key)
                )).all() if k[0]
            }

            for category, items in by_category.items():
                if only and category not in only:
                    continue
                total_svc = sum(i["svc_count"] for i in items)
                if total_svc < args.min_svc:
                    stats["categories_skipped"] += 1
                    continue
                if category == "OTHER":
                    # Решта без категорії — окрема обробка
                    continue

                profile_key = make_profile_canonical_key(category, items)
                # Збираємо name з топ-1 service
                top = max(items, key=lambda x: x["svc_count"])
                profile_name = top["names"][0] if top["names"] else category.replace("_", " ").title()

                # Підвантаж контенту
                content = load_content(category)
                if content:
                    stats["with_content"] += 1
                else:
                    content = placeholder_content(category, profile_name, total_svc, top["names"])
                    stats["with_placeholder"] += 1

                if profile_key in existing_keys:
                    # колізія — використовуємо synthetic fam_
                    profile_key = slugify_category(category)
                    if profile_key in existing_keys:
                        print(f"  ⚠ {category}: skip, ключ '{profile_key}' існує")
                        continue

                all_canonical_keys = [i["canonical_key"] for i in items]
                profile_id = str(uuid.uuid4())

                # Чи у категорії ≥2 brand-варіантів?
                all_brands = set()
                for it in items:
                    all_brands.update(it.get("brands") or [])

                print(f"  [{category}] profile_key='{profile_key}' name={content.get('name')!r} "
                      f"covers={len(items)} keys / {total_svc} svc / brands={list(all_brands)[:3]}")

                if args.apply:
                    session.add(ServiceProfile(
                        id=profile_id,
                        canonical_key=profile_key,
                        name=content.get("name", profile_name),
                        country=None,
                        default_language=LANGUAGE,
                        enabled=True,
                        created_by="rebuild_v2",
                        updated_by="rebuild_v2",
                    ))
                    session.add(ServiceProfileTranslation(
                        profile_id=profile_id,
                        language=LANGUAGE,
                        short_description=content["short_description"],
                        addresses_problems=content.get("addresses_problems", []),
                        target_audience=content.get("target_audience", []),
                        benefits=content.get("benefits", []),
                        keywords=content.get("keywords", []),
                        procedure_steps=content.get("procedure_steps", []),
                        contraindications=content.get("contraindications", []),
                        aftercare_advice=content.get("aftercare_advice"),
                        cross_sell=content.get("cross_sell", []),
                        sales_pitch=content.get("sales_pitch"),
                    ))
                    # Family-level option для всієї категорії (canonical_keys list)
                    session.add(ServiceProfileOption(
                        profile_id=profile_id,
                        option_type="family",
                        name=content.get("name", profile_name),
                        sort_order=0,
                        short_description=content["short_description"],
                        addresses_problems=content.get("addresses_problems", []),
                        target_audience=content.get("target_audience", []),
                        benefits=content.get("benefits", []),
                        keywords=content.get("keywords", []),
                        sales_pitch=content.get("sales_pitch"),
                        cross_sell=content.get("cross_sell", []),
                        procedure_steps=content.get("procedure_steps", []),
                        contraindications=content.get("contraindications", []),
                        aftercare_advice=content.get("aftercare_advice"),
                        canonical_keys=all_canonical_keys,
                    ))
                    stats["options_created"] += 1

                    # Brand-options (опційно): якщо є custom content з options
                    for opt in content.get("brand_options", []) or []:
                        session.add(ServiceProfileOption(
                            profile_id=profile_id,
                            option_type="brand",
                            name=opt["name"],
                            sort_order=opt.get("sort_order", 1),
                            short_description=opt.get("short_description", ""),
                            addresses_problems=opt.get("addresses_problems", []),
                            target_audience=opt.get("target_audience", []),
                            benefits=opt.get("benefits", []),
                            keywords=opt.get("keywords", []),
                            sales_pitch=opt.get("sales_pitch"),
                            cross_sell=opt.get("cross_sell", []),
                            procedure_steps=opt.get("procedure_steps", []),
                            contraindications=opt.get("contraindications", []),
                            aftercare_advice=opt.get("aftercare_advice"),
                            canonical_keys=opt.get("canonical_keys", []),
                        ))
                        stats["options_created"] += 1

                stats["created"] += 1
                existing_keys.add(profile_key)

            if args.apply:
                await session.commit()
    finally:
        await engine.dispose()

    print(f"\n=== DONE ===")
    print(f"Profiles created:    {stats['created']}")
    print(f"  з expert content:  {stats['with_content']}")
    print(f"  з placeholder:     {stats['with_placeholder']}")
    print(f"Options created:     {stats['options_created']}")
    print(f"Skipped (low coverage): {stats['categories_skipped']}")
    if not args.apply:
        print("\n(DRY RUN — use --apply)")


if __name__ == "__main__":
    asyncio.run(amain())
