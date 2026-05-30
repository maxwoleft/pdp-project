"""Створює catch-all MISC profile для всіх OTHER канонічних ключів.

Один profile + один option з canonical_keys=[всі OTHER keys] → 100% покриття.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileOption,
    ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory

CATEGORIZED = Path(__file__).parent.parent / ".logs" / "canonical_keys_categorized.json"

MISC_CONTENT = {
    "name": "Інші процедури мережі",
    "canonical_key": "fam_other",
    "short_description": (
        "Спеціалізовані процедури які пропонуються у певних салонах мережі — "
        "косметологічні, апаратні, авторські протоколи. Майстер уточнить деталі на консультації."
    ),
    "addresses_problems": [
        "потрібна нестандартна процедура",
        "клієнт описує специфічну потребу що не покрита базовими послугами",
        "запит на авторський протокол чи рідкісний бренд",
    ],
    "target_audience": [
        "клієнти, які шукають конкретний бренд або протокол",
        "регулярні клієнти салону з індивідуальною програмою",
    ],
    "benefits": [
        "розширений каталог процедур у певних локаціях",
        "консультація з майстром для точного підбору",
        "можливість записатись на специфічний бренд або апарат",
    ],
    "keywords": ["інша послуга", "спеціальна процедура", "індивідуальна програма"],
    "sales_pitch": (
        "Якщо вас цікавить конкретний протокол чи бренд, якого ви не знайшли в основному списку — "
        "уточніть назву послуги, я перевірю в якому салоні вона доступна."
    ),
    "cross_sell": [],
    "procedure_steps": [],
    "contraindications": [],
    "aftercare_advice": None,
}


async def main() -> None:
    data = json.loads(CATEGORIZED.read_text(encoding="utf-8"))
    other_items = data["by_category"].get("OTHER", [])
    other_keys = [it["canonical_key"] for it in other_items]
    print(f"OTHER canonical_keys: {len(other_keys)}, services: {sum(i['svc_count'] for i in other_items)}")

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            existing = (await session.execute(
                select(ServiceProfile).where(ServiceProfile.canonical_key == MISC_CONTENT["canonical_key"])
            )).scalar_one_or_none()
            if existing:
                print("MISC profile already exists, skipping")
                return

            pid = str(uuid.uuid4())
            session.add(ServiceProfile(
                id=pid,
                canonical_key=MISC_CONTENT["canonical_key"],
                name=MISC_CONTENT["name"],
                country=None,
                default_language="uk",
                enabled=True,
                created_by="rebuild_misc",
                updated_by="rebuild_misc",
            ))
            session.add(ServiceProfileTranslation(
                profile_id=pid,
                language="uk",
                short_description=MISC_CONTENT["short_description"],
                addresses_problems=MISC_CONTENT["addresses_problems"],
                target_audience=MISC_CONTENT["target_audience"],
                benefits=MISC_CONTENT["benefits"],
                keywords=MISC_CONTENT["keywords"],
                sales_pitch=MISC_CONTENT["sales_pitch"],
                cross_sell=MISC_CONTENT["cross_sell"],
                procedure_steps=[],
                contraindications=[],
                aftercare_advice=None,
            ))
            session.add(ServiceProfileOption(
                profile_id=pid,
                option_type="family",
                name=MISC_CONTENT["name"],
                sort_order=0,
                short_description=MISC_CONTENT["short_description"],
                addresses_problems=MISC_CONTENT["addresses_problems"],
                target_audience=MISC_CONTENT["target_audience"],
                benefits=MISC_CONTENT["benefits"],
                keywords=MISC_CONTENT["keywords"],
                sales_pitch=MISC_CONTENT["sales_pitch"],
                cross_sell=MISC_CONTENT["cross_sell"],
                procedure_steps=[],
                contraindications=[],
                aftercare_advice=None,
                canonical_keys=other_keys,
            ))
            await session.commit()
            print(f"Created MISC profile covering {len(other_keys)} canonical_keys")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
