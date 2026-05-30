"""Виокремлює LPG масаж у окремий profile з Endospheres / Endosphere.
Без LLM — content написаний Claude.
"""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


UK = {
    "short_description": "LPG масаж — апаратний роликово-вакуумний масаж за французькою технологією. Корекція фігури, антицелюлітний ефект, ліфтинг обличчя і тіла.",
    "addresses_problems": ["целюліт усіх ступенів", "локальні жирові відкладення", "набряклість і застійні зони", "втрата тонусу шкіри тіла", "вікові зміни овалу обличчя"],
    "target_audience": ["клієнти з целюлітом 1-3 ступеня", "перед сезоном або подією", "після пологів для відновлення", "клієнти 30+ для підтяжки шкіри"],
    "benefits": ["роликово-вакуумна технологія LPG (Франція)", "одночасна робота з жировою тканиною, лімфою і шкірою", "результат з курсу 8-10 сесій", "доступний для тіла і обличчя"],
    "keywords": ["lpg", "lpg масаж", "роликово-вакуумний масаж", "корекція фігури", "антицелюлітний", "ліфтинг"],
    "sales_pitch": "LPG — золотий стандарт апаратного антицелюлітного впливу. Раджу курс 10-12 сесій 2 рази на тиждень, потім підтримка раз на 2 тижні.",
    "cross_sell": ["обгортання у комплексі", "endospheres або kuma shape паралельно", "лімфодренажний масаж між сесіями"],
    "procedure_steps": ["переодягання у спеціальний костюм", "робота апаратом LPG по зонах", "поетапна обробка", "крем"],
    "contraindications": ["вагітність", "тромбофлебіт", "онкологія", "гострі запалення"],
    "aftercare_advice": "Питний режим 2-3 л води. Фізична активність 30 хв щодня для лімфотоку. Раджу курсом 10-12 сесій.",
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            for country in ("ua", "pl", "gb"):
                kr = await session.execute(text(f"""
                    SELECT DISTINCT canonical_key FROM {country}.service
                    WHERE archive=false AND (canonical_key ILIKE '%lpg%' OR name ILIKE '%lpg%')
                """))
                keys = sorted({row[0] for row in kr.all() if row[0]})
                if not keys:
                    print(f"  [{country}] no LPG keys — skip")
                    continue
                print(f"  [{country}] LPG keys: {keys}")

                exists = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND name='LPG масаж'"
                ), {"c": country})
                if exists.scalar():
                    print(f"  ⚠ LPG already exists for {country} — skip create")
                    continue

                primary = keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                ), {"c": country, "k": primary})
                if chk.scalar():
                    primary = f"lpg_{country}_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name="LPG масаж",
                    country=country, default_language="uk", enabled=True,
                    created_by="lpg_v1", updated_by="lpg_v1",
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **UK,
                ))
                session.add(ServiceProfileOption(
                    profile_id=pid, option_type="family", name="LPG масаж",
                    sort_order=0, canonical_keys=keys, **UK,
                    keywords_by_lang={
                        "uk": UK["keywords"],
                        "ru": ["lpg", "lpg массаж", "роликово-вакуумный массаж", "коррекция фигуры", "антицеллюлитный", "лифтинг"],
                        "en": ["lpg", "lpg massage", "roller-vacuum massage", "body sculpting", "anti-cellulite", "lifting"],
                        "pl": ["lpg", "masaż lpg", "masaż rolkowo-próżniowy", "modelowanie sylwetki", "antycellulit", "lifting"],
                    },
                ))
                await session.flush()
                print(f"  + created LPG масаж [{country}] with {len(keys)} keys + multilingual keywords")

                # Remove from Endospheres / catchall
                for target_name in ("Endospheres / Endosphere", "Інші апаратні процедури тіла"):
                    cat = await session.execute(text(
                        "SELECT o.id, o.canonical_keys FROM public.service_profile_option o "
                        "JOIN public.service_profile p ON p.id=o.profile_id "
                        "WHERE p.country=:c AND p.name=:n AND o.option_type='family'"
                    ), {"c": country, "n": target_name})
                    row = cat.first()
                    if row:
                        cleaned = [k for k in (row[1] or []) if k not in keys]
                        if len(cleaned) != len(row[1] or []):
                            await session.execute(text(
                                "UPDATE public.service_profile_option SET canonical_keys=CAST(:k AS jsonb), embedding=NULL WHERE id=:id"
                            ), {"k": json.dumps(cleaned), "id": row[0]})
                            print(f"  − removed {len(row[1] or []) - len(cleaned)} lpg keys from '{target_name}' [{country}]")

            await session.commit()
            for country in ("ua", "pl", "gb"):
                await session.execute(text(f"""
                    UPDATE {country}.service SET profile_id = NULL
                    WHERE archive=false AND (canonical_key ILIKE '%lpg%' OR name ILIKE '%lpg%')
                """))
                r = await session.execute(text(f"""
                    UPDATE {country}.service s SET profile_id = sub.profile_id
                    FROM (
                      SELECT DISTINCT ON (canonical_key) canonical_key, profile_id
                      FROM (
                        SELECT jsonb_array_elements_text(o.canonical_keys) AS canonical_key, o.profile_id
                        FROM public.service_profile_option o JOIN public.service_profile p ON p.id=o.profile_id
                        WHERE p.country=:c
                      ) x ORDER BY canonical_key, profile_id
                    ) sub
                    WHERE s.canonical_key = sub.canonical_key AND s.archive=false AND s.profile_id IS NULL
                """), {"c": country})
                print(f"  [{country}] re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
