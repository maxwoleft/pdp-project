"""Створює catch-all profiles per CRM root category — для 100% покриття.

Стратегія:
- Для кожного uncovered canonical_key — знаходимо root category (traverse parent_id).
- Group keys per root → catch-all profile per group.
- Створюємо profile + family option з усіма keys групи.
- Sales pitch: загальний з натяком на консультацію майстра.

При AI пошуку:
- Specific profile має вищий score (точніший addresses_problems match).
- Catch-all — fallback (low score), але coverage гарантована.

Запуск:
    python -m scripts.create_catchall_profiles                # dry
    python -m scripts.create_catchall_profiles --apply
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption,
)
from app.infrastructure.db.session import build_engine, build_session_factory


# Маппінг CRM root category name → catch-all profile.
ROOT_TO_CATCHALL: dict[str, str] = {
    "волосся": "Інші процедури для волосся",
    "нарощування волосся": "Інші процедури для волосся",
    "нігтьовий сервіс": "Інші процедури нігтьового сервісу",
    "подологія": "Інші процедури подології",
    "косметологія": "Інші процедури косметології",
    "ін'єкційна косметологія": "Інші процедури косметології",
    "endospheres": "Інші апаратні процедури тіла",
    "endosphere": "Інші апаратні процедури тіла",
    "icoone laser": "Інші апаратні процедури тіла",
    "kuma shape": "Інші апаратні процедури тіла",
    "lpg масаж": "Інші апаратні процедури тіла",
    "robolex": "Інші апаратні процедури тіла",
    "макіяж, брови, вії": "Інші процедури з брів, вій та макіяжу",
    "перманент": "Перманентний макіяж",
    "масаж": "Інші види масажу",
    "депіляція": "Інші процедури депіляції",
    "лазерна епіляція (жінки)": "Інші процедури депіляції",
    "шугаринг": "Інші процедури депіляції",
    "шугарінг/ віск": "Інші процедури депіляції",
    "шугарінг": "Інші процедури депіляції",
    "віск": "Інші процедури депіляції",
    "чоловічі послуги": "Інші процедури для чоловіків",
    "обгортання": "Інші процедури обгортання",
    "консультації": "Інші консультації",
    "консультація": "Інші консультації",
    # PL extra roots
    "обгортання styx": "Інші процедури обгортання",
    "обгортування": "Інші процедури обгортання",
    "лазерна косметологія": "Інші процедури косметології",
    "лазерна епіляція": "Інші процедури депіляції",
    "апаратна косметологія": "Інші апаратні процедури тіла",
}


# GB multilingual roots ("EN HAIR / UA Волосся / RUS Волосы") — keyword match
GB_KEYWORD_MAP: list[tuple[str, str]] = [
    (r"hair|волос", "Інші процедури для волосся"),
    (r"nail|манікюр|педикюр|маникюр", "Інші процедури нігтьового сервісу"),
    (r"podology|подологі|подоло", "Інші процедури подології"),
    (r"icoone|stratosphere|айкун|стратосфер|endo|lpg|kuma", "Інші апаратні процедури тіла"),
    (r"make[ _]?up|візаж|визаж|brow|lash", "Інші процедури з брів, вій та макіяжу"),
    (r"massage|масаж|массаж", "Інші види масажу"),
    (r"men|чолові|мужчин", "Інші процедури для чоловіків"),
    (r"epil|wax|sugar|депіляц|депиляц|шугар", "Інші процедури депіляції"),
    (r"cosmetolog|косметолог", "Інші процедури косметології"),
    (r"wrap|обгорт", "Інші процедури обгортання"),
    (r"offer|propos|пропозиц|предложен", "Інші консультації"),
    (r"consult|консультаці", "Інші консультації"),
]


def map_root(root_name: str | None) -> str | None:
    if not root_name:
        return None
    import re as _re
    key = root_name.lower().strip()
    if key in ROOT_TO_CATCHALL:
        return ROOT_TO_CATCHALL[key]
    for pat, ca in GB_KEYWORD_MAP:
        if _re.search(pat, key, _re.IGNORECASE):
            return ca
    return None


# Контент для кожного catch-all.
CONTENT: dict[str, dict] = {
    "Інші процедури для волосся": {
        "short_description": "Спеціальні і брендові процедури догляду за волоссям — Hadat, Dr.Sorbie, ORising, Innovatis, Napla, LANZA, LINK-D та інші.",
        "addresses_problems": ["потрібна специфічна брендова процедура для волосся", "конкретний бренд чи техніка не входить у стандарт"],
        "target_audience": ["клієнти що шукають конкретний бренд або процедуру"],
        "benefits": ["широкий вибір брендових засобів", "майстер підбере процедуру індивідуально"],
        "keywords": ["брендові процедури", "спеціальні догляди", "hadat", "dr sorbie", "innovatis", "lanza", "napla"],
        "sales_pitch": "У нас доступні різні брендові процедури для волосся. Раджу попередньо консультацію з майстром — підбере найкраще під ваш стан.",
        "cross_sell": ["консультація майстра", "діагностика стану волосся"],
    },
    "Інші процедури нігтьового сервісу": {
        "short_description": "Спеціальні процедури і дизайни нігтьового сервісу — нестандартні техніки, бренди, дизайни.",
        "addresses_problems": ["потрібна нестандартна процедура нігтьового сервісу", "конкретний дизайн чи бренд"],
        "target_audience": ["клієнти з конкретним запитом на дизайн або процедуру"],
        "benefits": ["майстри з широким досвідом", "індивідуальний підбір"],
        "keywords": ["нестандартні процедури", "спеціальні техніки нігтів", "брендові гель-лаки"],
        "sales_pitch": "Майстри салону мають широкий спектр спеціальних процедур. Раджу консультацію для підбору під ваш запит.",
        "cross_sell": ["консультація майстра", "стандартний манікюр/педикюр"],
    },
    "Інші процедури подології": {
        "short_description": "Спеціальні процедури подології — корекційні системи, ортези, специфічні обробки.",
        "addresses_problems": ["складний випадок зі стопою", "специфічна корекційна система"],
        "target_audience": ["клієнти зі специфічними подологічними потребами"],
        "benefits": ["сертифіковані подологи", "індивідуальний підхід"],
        "keywords": ["складні випадки", "корекційні системи", "ортези"],
        "sales_pitch": "Подолог підбере індивідуальне рішення під ваш випадок. Раджу консультацію подолога.",
        "cross_sell": ["консультація подолога", "подологічний педикюр"],
    },
    "Інші процедури косметології": {
        "short_description": "Спеціальні косметологічні процедури — інʼєкційні, брендові маски, аппаратні.",
        "addresses_problems": ["потрібна специфічна косметологічна процедура", "конкретний бренд чи протокол"],
        "target_audience": ["клієнти зі специфічними цілями краси"],
        "benefits": ["широкий вибір протоколів", "лікар-косметолог підбере індивідуально"],
        "keywords": ["спеціальні процедури", "брендові маски", "інʼєкційна косметологія"],
        "sales_pitch": "Спершу консультація косметолога — підбере процедуру під ваш стан і ціль. Раджу не пропускати консультацію.",
        "cross_sell": ["консультація косметолога", "діагностика шкіри"],
    },
    "Інші апаратні процедури тіла": {
        "short_description": "Апаратні процедури корекції фігури і догляду за тілом — Endospheres, Icoone Laser, Kuma Shape, LPG, Robolex.",
        "addresses_problems": ["корекція фігури неінвазивно", "локальні жирові відкладення", "проблеми з тонусом шкіри тіла"],
        "target_audience": ["клієнти для корекції фігури", "перед сезоном", "після пологів"],
        "benefits": ["широкий вибір апаратів під різні задачі", "видимий результат курсом", "консультація для підбору"],
        "keywords": ["апаратна корекція", "endospheres", "icoone", "kuma shape", "lpg", "robolex"],
        "sales_pitch": "Підбір апарату залежить від вашої задачі. Раджу консультацію — підкажемо який варіант ефективніший для вас.",
        "cross_sell": ["консультація косметолога", "обгортання у комплексі"],
    },
    "Інші процедури з брів, вій та макіяжу": {
        "short_description": "Спеціальні процедури догляду за бровами, віями, нестандартні види макіяжу.",
        "addresses_problems": ["потрібна специфічна процедура з брів/вій", "нестандартний макіяж"],
        "target_audience": ["клієнти з конкретним запитом на брови/вії/макіяж"],
        "benefits": ["майстри з досвідом у спеціальних техніках", "підбір під ваш тип"],
        "keywords": ["спеціальні процедури брови", "вії", "макіяж", "догляд"],
        "sales_pitch": "Майстер підбере процедуру з широкого спектру. Раджу попередню консультацію.",
        "cross_sell": ["консультація майстра", "ламінування / корекція"],
    },
    "Перманентний макіяж": {
        "short_description": "Перманентний макіяж — брови, губи, повіки. Тримається 1-2 роки.",
        "addresses_problems": ["хочеться постійно гарних брів/губ без щоденного мейку", "рідкі брови чи асиметрія", "тонкі губи — хочеться контуру"],
        "target_audience": ["клієнти що економлять час на щоденний макіяж", "перед поїздкою у відпустку"],
        "benefits": ["тримається 1-2 роки", "природний результат при правильній техніці", "огляд через 4-6 тижнів для корекції"],
        "keywords": ["перманентний макіяж", "татуаж", "брови перманент", "губи перманент", "напилення"],
        "sales_pitch": "Перманент економить час на щоденний макіяж. Раджу консультацію — обговоримо форму і відтінок під вас.",
        "cross_sell": ["корекція через 4-6 тижнів", "догляд за зоною після"],
    },
    "Інші види масажу": {
        "short_description": "Спеціальні види масажу — тайський, медовий, ANTI-AGE, GOLD техніка, локальні зони.",
        "addresses_problems": ["хочу нестандартний вид масажу", "локальна зона напруги", "релакс з ароматерапією"],
        "target_audience": ["клієнти на регулярному догляді тілом", "перед подією для розслаблення"],
        "benefits": ["широкий вибір технік", "індивідуальний підбір під стан"],
        "keywords": ["тайський масаж", "медовий", "анти-ейдж", "gold масаж", "локальний масаж"],
        "sales_pitch": "Підбір масажу залежить від вашого стану і цілі. Раджу консультацію з масажистом для вибору.",
        "cross_sell": ["регулярний візит", "комбінація з SPA"],
    },
    "Інші процедури депіляції": {
        "short_description": "Спеціальні зони і техніки депіляції — окремі зони лазером, воском, шугарингом.",
        "addresses_problems": ["потрібна депіляція специфічної зони", "хочу спробувати техніку"],
        "target_audience": ["клієнти що хочуть очистити конкретну зону", "перед відпусткою"],
        "benefits": ["широкий вибір зон і технік", "майстри з досвідом"],
        "keywords": ["лазер", "віск", "шугаринг", "епіляція", "зони"],
        "sales_pitch": "Кожна зона потребує різного підходу. Раджу попередню консультацію.",
        "cross_sell": ["комбінація зон у комплексі"],
    },
    "Інші процедури для чоловіків": {
        "short_description": "Спеціальні чоловічі послуги — варіанти стрижок, бороди, доглядів.",
        "addresses_problems": ["потрібен специфічний чоловічий формат послуги"],
        "target_audience": ["чоловіки на регулярному догляді"],
        "benefits": ["майстри-барбери з досвідом", "повний комплекс чоловічих послуг"],
        "keywords": ["чоловіча стрижка", "борода", "вуса", "чоловічий догляд"],
        "sales_pitch": "Підбір під ваш формат. Раджу консультацію з барбером для першого візиту.",
        "cross_sell": ["комплекс стрижка + борода", "регулярний візит"],
    },
    "Інші процедури обгортання": {
        "short_description": "Спеціальні види обгортань для корекції фігури і детоксу.",
        "addresses_problems": ["целюліт", "потрібен детокс", "корекція фігури"],
        "target_audience": ["клієнти на курсі корекції фігури"],
        "benefits": ["різні бренди й типи обгортань", "комбінується з масажем"],
        "keywords": ["обгортання", "detox", "anticell", "drain"],
        "sales_pitch": "Обгортання курсом дає виразний результат. Раджу 8-10 процедур раз на тиждень.",
        "cross_sell": ["антицелюлітний масаж", "endospheres у комплексі"],
    },
    "Інші консультації": {
        "short_description": "Спеціальні консультації майстрів і спеціалістів салону краси.",
        "addresses_problems": ["потрібна професійна оцінка", "не впевнений у виборі"],
        "target_audience": ["нові клієнти", "перед курсом"],
        "benefits": ["професійна оцінка", "індивідуальний план"],
        "keywords": ["консультація", "діагностика"],
        "sales_pitch": "Раджу консультацію — допоможе уникнути зайвих витрат на неправильно обрану процедуру.",
        "cross_sell": ["перша процедура за рекомендацією майстра"],
    },
}


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--country", default="ua")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Covered keys
            covered_rows = await session.execute(text(f"""
                SELECT canonical_key FROM public.service_profile WHERE country='{args.country}' AND canonical_key IS NOT NULL
                UNION
                SELECT jsonb_array_elements_text(o.canonical_keys)
                FROM public.service_profile_option o
                JOIN public.service_profile p ON p.id=o.profile_id
                WHERE p.country='{args.country}' AND o.canonical_keys IS NOT NULL
            """))
            covered = {r[0] for r in covered_rows.all() if r[0]}

            # Для кожного uncovered key — знаходимо root category назву через recursive CTE
            rows = await session.execute(text(f"""
                WITH RECURSIVE cat_tree AS (
                    SELECT id, name, parent_id, id AS leaf_id
                    FROM {args.country}.category WHERE archive=false
                    UNION ALL
                    SELECT c.id, c.name, c.parent_id, t.leaf_id
                    FROM {args.country}.category c
                    JOIN cat_tree t ON t.parent_id = c.id
                    WHERE c.archive=false
                ),
                roots AS (
                    SELECT leaf_id, name AS root_name
                    FROM cat_tree
                    WHERE parent_id IS NULL
                )
                SELECT s.canonical_key, MIN(s.name) AS sample, COUNT(*) AS cnt, MIN(r.root_name) AS root_name
                FROM {args.country}.service s
                JOIN roots r ON r.leaf_id = s.category_id
                WHERE s.archive=false AND s.canonical_key IS NOT NULL
                GROUP BY s.canonical_key
            """))

            buckets: dict[str, list[str]] = defaultdict(list)
            unclassified_keys: list[tuple[str, str, int, str]] = []
            for k, sample, cnt, root in rows.all():
                if k in covered:
                    continue
                catchall = map_root(root)
                if catchall:
                    buckets[catchall].append(k)
                else:
                    unclassified_keys.append((k, sample, cnt, root))

            print(f"Uncovered keys mapped to catch-alls:")
            for ca, ks in sorted(buckets.items(), key=lambda x: -len(x[1])):
                print(f"  {ca:50s} +{len(ks)} keys")
            print(f"\nUnclassified (no catch-all for root): {len(unclassified_keys)}")
            for k, n, c, r in unclassified_keys[:15]:
                print(f"  root={r!r:30s} {c:3d} {k[:50]}")

            if not args.apply:
                print("\nDRY RUN")
                return

            created = 0
            for catchall, keys in buckets.items():
                # Check if profile already exists
                exists = await session.execute(text("""
                    SELECT id FROM public.service_profile
                    WHERE country=:c AND name=:n
                """), {"c": args.country, "n": catchall})
                pid = exists.scalar()
                if pid:
                    # Just extend family option
                    opt_row = await session.execute(text("""
                        SELECT id, canonical_keys FROM public.service_profile_option
                        WHERE profile_id=:p AND option_type='family'
                    """), {"p": pid})
                    row = opt_row.first()
                    if row:
                        existing_keys = list(row[1] or [])
                        merged = list(dict.fromkeys(existing_keys + keys))
                        import json as _json
                        await session.execute(text("""
                            UPDATE public.service_profile_option
                            SET canonical_keys = CAST(:k AS jsonb), embedding = NULL
                            WHERE id = :oid
                        """), {"k": _json.dumps(merged), "oid": row[0]})
                        print(f"  ↻ extended {catchall}: {len(existing_keys)} → {len(merged)}")
                else:
                    slug = catchall.lower().replace(" ", "_").replace(",", "").replace("/", "_").replace("'", "")
                    placeholder = f"catchall_{slug[:60]}"
                    c = CONTENT.get(catchall, {})
                    pid = str(uuid.uuid4())
                    session.add(ServiceProfile(
                        id=pid, canonical_key=placeholder, name=catchall,
                        country=args.country, default_language="uk", enabled=True,
                        created_by="catchall_v1", updated_by="catchall_v1",
                    ))
                    from app.infrastructure.db.models.profile import ServiceProfileTranslation
                    session.add(ServiceProfileTranslation(
                        profile_id=pid, language="uk",
                        short_description=c.get("short_description", catchall),
                        addresses_problems=c.get("addresses_problems", []),
                        target_audience=c.get("target_audience", []),
                        benefits=c.get("benefits", []),
                        keywords=c.get("keywords", []),
                        sales_pitch=c.get("sales_pitch"),
                        cross_sell=c.get("cross_sell", []),
                    ))
                    session.add(ServiceProfileOption(
                        profile_id=pid, option_type="family",
                        name=catchall, sort_order=0,
                        short_description=c.get("short_description", catchall),
                        addresses_problems=c.get("addresses_problems", []),
                        target_audience=c.get("target_audience", []),
                        benefits=c.get("benefits", []),
                        keywords=c.get("keywords", []),
                        sales_pitch=c.get("sales_pitch"),
                        cross_sell=c.get("cross_sell", []),
                        canonical_keys=keys,
                    ))
                    created += 1
                    print(f"  + created {catchall} with {len(keys)} keys")
                await session.flush()

            await session.commit()
            print(f"\nDONE: created {created} new catch-all profiles")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
