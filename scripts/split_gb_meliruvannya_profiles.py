"""GB: розколоти "Інші процедури для волосся" + "Мелірування" на 4 окремих profiles:
Airtouch, Balayage, Highlights, Shatush. UK content inline."""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


PROFILES = [
    {
        "name": "Airtouch",
        "keys": [
            "airtouch_full_head_meliruvannya_sushka_tonuvannya",
            "airtouch_half_head_meliruvannya_sushka_tonuvannya",
            "airtouch_meliruvannya_section_sushka_t_tonuvannya",
        ],
        "uk": {
            "short_description": "Airtouch — техніка мелірування з продуванням фену: коротші волоски виходять, освітлюються тільки довгі пасма. Природний м'який gradient, мінімальна корекція раз на 4-6 місяців.",
            "addresses_problems": [
                "хочеться природного багатотонного блонду",
                "набридло часто оновлювати коріння",
                "потрібен м'який gradient без чіткої межі",
                "тонке волосся — хочеться візуального обʼєму"
            ],
            "target_audience": [
                "власниці натурального русявого/світло-русявого волосся",
                "хто хоче мінімум підтримки (раз на 4-6 місяців)",
                "молодий природний look"
            ],
            "benefits": [
                "природний рост — не видно границю",
                "корекція раз на 4-6 місяців замість 2",
                "візуальний обʼєм для тонкого волосся",
                "доступні: full head / half head / t-section + тонування REDKEN + сушка"
            ],
            "keywords": ["airtouch", "ейртач", "мелірування airtouch", "природне мелірування", "русяве мелірування"],
            "sales_pitch": "Якщо хочете природний блонд з мінімумом походів у салон — Airtouch ідеальний. Раджу half head для першого разу.",
            "cross_sell": ["тонування REDKEN у вартості", "догляд преміум-брендами після", "регулярна корекція раз на 4-6 місяців"],
            "procedure_steps": ["діагностика волосся і обговорення відтінку", "розділення на пасма", "продування фену для відсіву коротких волосків", "освітлення обраних пасем", "тонування REDKEN", "сушка"],
            "contraindications": ["агресивно пошкоджене волосся (спершу відновлення)"],
            "aftercare_advice": "Шампунь без сульфатів. Маска для блонду раз на тиждень. Корекція раз на 4-6 місяців."
        }
    },
    {
        "name": "Balayage",
        "keys": [
            "balayage_full_head_meliruvannya_sushka_tonuvannya",
            "balayage_half_head_meliruvannya_sushka_tonuvannya",
            "balayage_half_head_melyrovanye_sushka_tonyrovanye",
            "balayage_meliruvannya_section_sushka_t_tonuvannya",
        ],
        "uk": {
            "short_description": "Balayage — техніка вільної руки: майстер розмашисто наносить фарбу від середини довжини до кінчиків. Природний sun-kissed gradient.",
            "addresses_problems": [
                "хочеться розмитий гра́дієнт без чіткої лінії",
                "коренева зона має лишатись натуральною",
                "набридло однотонне фарбування",
                "хочеться sun-kissed ефекту"
            ],
            "target_audience": [
                "клієнтки що цінують природний нерегулярний gradient",
                "хочуть зберегти натуральний колір коренів",
                "хочуть style як після відпустки"
            ],
            "benefits": [
                "розмитий природний gradient",
                "корекція раз на 3-4 місяці",
                "майстерська ручна техніка",
                "доступні: full head / half head / t-section + тонування + сушка"
            ],
            "keywords": ["balayage", "балаяж", "мелірування balayage", "природне освітлення", "sun-kissed"],
            "sales_pitch": "Якщо хочете легкий нерегулярний gradient як після літа — Balayage. Раджу half head для м'якого старту, full head — для більш виразного результату.",
            "cross_sell": ["тонування у вартості", "догляд для пофарбованого волосся вдома", "корекція раз на 3-4 місяці"],
            "procedure_steps": ["діагностика і вибір відтінку", "розділення на робочі сектори", "ручне нанесення фарби від середини до кінчиків", "тонування за бажанням", "сушка"],
            "contraindications": ["сильно пошкоджене волосся (підготувати спершу)"],
            "aftercare_advice": "Шампунь без сульфатів. Догляд преміум-брендами. Корекція раз на 3-4 місяці."
        }
    },
    {
        "name": "Highlights",
        "keys": [
            "full_head_highlights_meliruvannya_sushka_tonuvannya",
            "half_head_highlights_meliruvannya_sushka_tonuvannya",
            "highlights_meliruvannya_section_sushka_t_tonuvannya",
        ],
        "uk": {
            "short_description": "Highlights — класичне фольгове мелірування. Окремі тонкі пасма освітлюються по всій довжині. Чіткий високий контраст, стабільний результат.",
            "addresses_problems": [
                "хочеться чіткого виразного освітлення",
                "потрібна максимальна світла насиченість",
                "коренева зона — теж освітлена",
                "класичний look без вільної техніки"
            ],
            "target_audience": [
                "клієнтки що хочуть високий світлий рівень",
                "класичний look без natural-mess",
                "ті, хто звик до фольгового мелірування"
            ],
            "benefits": [
                "чіткий контраст і високий світловий рівень",
                "по всій довжині (включно з коренями)",
                "доступні: full head / half head / t-section + тонування REDKEN + сушка"
            ],
            "keywords": ["highlights", "хайлайт", "мелірування фольгове", "класичне мелірування", "освітлення волосся"],
            "sales_pitch": "Якщо хочете класичне освітлене мелірування — Highlights. Half head для часткового, full head для повного. Підтримка раз на 2-3 місяці.",
            "cross_sell": ["тонування REDKEN у вартості", "догляд для блонду після", "корекція раз на 2-3 місяці"],
            "procedure_steps": ["вибір рівня освітлення", "розподіл волосся на тонкі пасма у фользі", "освітлення", "тонування", "сушка"],
            "contraindications": ["пошкоджене волосся (відновлення спершу)"],
            "aftercare_advice": "Шампунь без сульфатів. Маска для блонду. Корекція раз на 2-3 місяці."
        }
    },
    {
        "name": "Shatush",
        "keys": [
            "full_head_meliruvannya_shatush_sushka_tonuvannya",
            "half_head_meliruvannya_shatush_sushka_tonuvannya",
            "meliruvannya_section_shatush_sushka_t_tonuvannya",
        ],
        "uk": {
            "short_description": "Shatush — техніка з начісом для м'якого розмитого gradient. Освітлення зосереджене на середині-кінчиках. Природний ефект сонячного волосся без чіткої межі.",
            "addresses_problems": [
                "хочеться м'якого розмитого gradient",
                "натуральне коріння без overlay",
                "ефект sun-kissed без яскравих ліній",
                "тонке волосся — хочеться обʼєму"
            ],
            "target_audience": [
                "клієнтки що хочуть delicate gradient",
                "темне волосся — м'яке посvітління кінців",
                "low maintenance догляд"
            ],
            "benefits": [
                "природний gradient без чіткої межі",
                "корекція раз на 4-5 місяців",
                "техніка з начісом — унікальний ефект",
                "доступні: full head / half head / t-section + тонування + сушка"
            ],
            "keywords": ["shatush", "шатуш", "мелірування shatush", "природне освітлення кінців", "delicate gradient"],
            "sales_pitch": "Shatush — для м'якого натурального ефекту з мінімумом підтримки. Раджу half head для першого досвіду, full head — для виразнішого результату.",
            "cross_sell": ["тонування у вартості", "догляд преміум-брендами", "корекція раз на 4-5 місяців"],
            "procedure_steps": ["діагностика", "розділення на пасма + начіс", "освітлення зосереджене на середині-кінцях", "тонування", "сушка"],
            "contraindications": ["сильно пошкоджене волосся"],
            "aftercare_advice": "Шампунь без сульфатів. Догляд для блонду. Корекція раз на 4-5 місяців."
        }
    },
]


REMOVE_FROM = ["Інші процедури для волосся", "Мелірування"]


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            all_keys: list[str] = []
            for prof in PROFILES:
                ex = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": prof["name"]})
                if ex.scalar():
                    print(f"  ⚠ {prof['name']} GB exists — skip create")
                    all_keys.extend(prof["keys"])
                    continue

                # Real keys
                kr = await session.execute(text(
                    "SELECT DISTINCT canonical_key FROM gb.service "
                    "WHERE archive=false AND canonical_key = ANY(:k)"
                ), {"k": prof["keys"]})
                real_keys = sorted({row[0] for row in kr.all() if row[0]})
                if not real_keys:
                    print(f"  ⚠ {prof['name']} GB: no real keys — skip")
                    continue

                primary = real_keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
                ), {"k": primary})
                if chk.scalar():
                    primary = f"{prof['name'].lower()}_gb_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name=prof["name"],
                    country="gb", default_language="uk", enabled=True,
                    created_by="gb_meliru_v1", updated_by="gb_meliru_v1",
                    canonical_keys=real_keys,
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **prof["uk"],
                ))
                await session.flush()
                print(f"  + created {prof['name']} GB with {len(real_keys)} keys")
                all_keys.extend(real_keys)

            # Remove keys з REMOVE_FROM profiles
            for target_name in REMOVE_FROM:
                r = await session.execute(text(
                    "SELECT id, canonical_keys FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": target_name})
                row = r.first()
                if row:
                    cleaned = [k for k in (row[1] or []) if k not in all_keys]
                    if len(cleaned) != len(row[1] or []):
                        await session.execute(text(
                            "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                        ), {"k": json.dumps(cleaned), "id": row[0]})
                        print(f"  − removed {len(row[1] or []) - len(cleaned)} keys з '{target_name}' GB")

            await session.commit()

            # Re-link
            await session.execute(text(
                "UPDATE gb.service SET profile_id = NULL WHERE archive=false AND canonical_key = ANY(:k)"
            ), {"k": all_keys})
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
