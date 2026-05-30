"""GB: окремі profiles для Icoone Laser і Stratosphere. Content inline без LLM.
Витягнути ці keys з catchall "Інші апаратні процедури тіла" GB.
Re-link service.profile_id.
"""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


ICOONE_KEYS = [
    "lazer",
    "lazer_pershyi_seans",
    "liftyng_oblychchya",
    "konsultatsiya_probna_sesiya",
]

ICOONE_UK = {
    "short_description": "Icoone Laser — LED + вакуумний апаратний масаж з лазером. Робота з целюлітом, лімфатичним застоєм, ліфтинг обличчя. Доступні сесії 45/60/80 хв, пробна 30 хв з консультацією.",
    "addresses_problems": [
        "фіброзний целюліт що не реагує на класичні методи",
        "хронічна набряклість ніг",
        "вікові зміни обличчя — потрібен ліфтинг",
        "локальні жирові відкладення",
        "втрата тонусу шкіри тіла",
    ],
    "target_audience": [
        "клієнти з целюлітом 3-4 ступеня",
        "регулярний курс корекції фігури",
        "омолодження обличчя 35+",
        "клієнти що хочуть спробувати — пробна сесія 30 хв"
    ],
    "benefits": [
        "комбінація LED + вакуум + лазер",
        "ефект на фіброзному целюліті",
        "опція ліфтинг обличчя 30 хв",
        "пробна сесія 30 хв з консультацією для першого знайомства",
        "італійська технологія Icoone"
    ],
    "keywords": [
        "icoone", "icoone laser", "айкун", "лазерний масаж",
        "проти фіброзного целюліту", "ліфтинг обличчя icoone",
        "пробна сесія icoone", "айкун лазер"
    ],
    "sales_pitch": "Icoone Laser — для випадків коли інші методи не дали результату. Раджу спершу пробну сесію 30 хв з консультацією — підберемо протокол. Курс 10-15 процедур 2 рази на тиждень.",
    "cross_sell": [
        "Stratosphere паралельно",
        "консультація для підбору протоколу",
        "обгортання у комплексі"
    ],
    "procedure_steps": [
        "консультація і фотофіксація",
        "переодягання",
        "робота апаратом з різними насадками (LED/вакуум/лазер)",
        "поетапна обробка зон"
    ],
    "contraindications": ["вагітність", "тромбофлебіт", "онкологія", "гострі запалення"],
    "aftercare_advice": "Питний режим. Уникайте сонця у оброблених зонах 24 години. Раджу курс 10-15 процедур."
}

STRATO_KEYS = [
    "pershyi_seans_stratosfery",
    "odnorazova_sesiya_stratosfera",
    "konsultatsiya_probna_sesiya_stratosfera",
]

STRATO_UK = {
    "short_description": "Stratosphere — апаратне моделювання тіла з ефектом ліфтингу і корекції контуру. Доступні разові сесії 45/60/80 хв, перший сеанс 60/80 хв, пробна 30 хв з консультацією.",
    "addresses_problems": [
        "втрата тонусу шкіри тіла",
        "локальні жирові відкладення",
        "потреба у корекції контуру неінвазивно",
        "після пологів — відновлення",
        "перед подією — швидкий результат"
    ],
    "target_audience": [
        "клієнти 30+ для підтримки тонусу",
        "після пологів",
        "перед подією",
        "клієнти що хочуть спробувати — пробна сесія 30 хв"
    ],
    "benefits": [
        "апаратне моделювання тіла",
        "помітний ефект з першого сеансу",
        "комбінується з масажем",
        "доступні разові сесії або курс",
        "пробна сесія 30 хв з консультацією для першого знайомства"
    ],
    "keywords": [
        "stratosphere", "стратосфера", "body sculpting",
        "корекція фігури", "перший сеанс стратосфера",
        "пробна сесія стратосфера"
    ],
    "sales_pitch": "Stratosphere — апаратна підтяжка тіла. Раджу спершу пробну сесію 30 хв з консультацією. Курс 6-8 процедур раз на тиждень для виразного результату.",
    "cross_sell": [
        "Icoone Laser паралельно",
        "консультація для підбору курсу",
        "обгортання у комплексі"
    ],
    "procedure_steps": [
        "консультація",
        "переодягання",
        "робота апаратом по зонам",
        "поетапна обробка"
    ],
    "contraindications": ["вагітність", "металеві імпланти у зоні", "онкологія"],
    "aftercare_advice": "Питний режим. Помірна фізична активність для закріплення ефекту."
}


PROFILES_TO_CREATE = [
    ("Icoone Laser", ICOONE_KEYS, ICOONE_UK),
    ("Stratosphere", STRATO_KEYS, STRATO_UK),
]

EXTRACT_FROM_PROFILES = ["Інші апаратні процедури тіла", "Endospheres / Endosphere"]


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            all_extracted_keys = set()
            for name, keys, uk in PROFILES_TO_CREATE:
                ex = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": name})
                if ex.scalar():
                    print(f"  ⚠ {name} GB вже існує — skip")
                    continue

                # Real keys у gb.service
                kr = await session.execute(text(
                    "SELECT DISTINCT canonical_key FROM gb.service "
                    "WHERE archive=false AND canonical_key = ANY(:k)"
                ), {"k": keys})
                real_keys = sorted({row[0] for row in kr.all() if row[0]})
                if not real_keys:
                    print(f"  ⚠ {name} GB: no real keys — skip")
                    continue

                primary = real_keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
                ), {"k": primary})
                if chk.scalar():
                    primary = f"{name.lower().replace(' ', '_')}_gb_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name=name,
                    country="gb", default_language="uk", enabled=True,
                    created_by="gb_apparatus_v1", updated_by="gb_apparatus_v1",
                    canonical_keys=real_keys,
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **uk,
                ))
                await session.flush()
                print(f"  + created [{name}] GB with {len(real_keys)} keys")
                all_extracted_keys.update(real_keys)

            # Remove these keys from catchall/Endospheres GB
            for target in EXTRACT_FROM_PROFILES:
                r = await session.execute(text(
                    "SELECT id, canonical_keys FROM service_profile "
                    "WHERE country='gb' AND name=:n"
                ), {"n": target})
                row = r.first()
                if row:
                    cleaned = [k for k in (row[1] or []) if k not in all_extracted_keys]
                    if len(cleaned) != len(row[1] or []):
                        await session.execute(text(
                            "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                        ), {"k": json.dumps(cleaned), "id": row[0]})
                        print(f"  − removed {len(row[1] or []) - len(cleaned)} keys з '{target}' GB")

            await session.commit()

            # Re-link gb.service.profile_id для уражених keys
            await session.execute(text("""
                UPDATE gb.service SET profile_id = NULL
                WHERE archive=false AND canonical_key = ANY(:k)
            """), {"k": list(all_extracted_keys)})
            r = await session.execute(text("""
                UPDATE gb.service s SET profile_id = sub.profile_id
                FROM (
                  SELECT DISTINCT ON (canonical_key) canonical_key, id AS profile_id
                  FROM (
                    SELECT jsonb_array_elements_text(p.canonical_keys) AS canonical_key, p.id
                    FROM public.service_profile p WHERE p.country='gb'
                  ) x ORDER BY canonical_key, profile_id
                ) sub
                WHERE s.canonical_key = sub.canonical_key AND s.archive=false AND s.profile_id IS NULL
            """))
            print(f"  ↻ gb re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
