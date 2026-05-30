"""Виносить Kuma Shape у окремий profile з Endospheres / Endosphere.
Створює UA-content + 3 переклади (RU/EN/PL) inline (без LLM).
Re-link service.profile_id.
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
    "short_description": "Kuma Shape — інноваційний апарат для корекції фігури з технологією багатовекторної компресії. Працює з локальними жировими відкладеннями і шкірним тонусом.",
    "addresses_problems": ["локальні жирові відкладення на стегнах, сідницях, животі", "втрата тонусу шкіри тіла", "целюліт середнього і важкого ступеня", "набряклість ніг"],
    "target_audience": ["клієнти з локальними проблемними зонами", "перед сезоном відкритого одягу", "після пологів для відновлення"],
    "benefits": ["багатовекторна компресія для глибокого впливу", "доступні зони: стегна (передня/задня/внутрішня/повністю), сідниці, поперек, спина, ноги, руки (трицепс)", "ефект з курсу 8-10 сесій", "поєднується з обгортанням і масажем"],
    "keywords": ["kuma shape", "кума шейп", "корекція фігури", "целюліт", "локальні жирові відкладення"],
    "sales_pitch": "Kuma Shape — для точкової роботи з проблемною зоною. Раджу курс 10-12 сесій 2 рази на тиждень. Підтримка раз на 2 тижні після курсу.",
    "cross_sell": ["обгортання у комплексі", "антицелюлітний масаж", "endospheres паралельно", "питний режим + активність"],
    "procedure_steps": ["консультація і вибір зони", "переодягання у спеціальний костюм", "робота апаратом по обраній зоні", "завершальна релаксація"],
    "contraindications": ["вагітність", "тромбофлебіт", "онкологія", "гострі запалення"],
    "aftercare_advice": "Питний режим 2-3 л води. Фізична активність 30 хв щодня. Раджу курс 10-12 сесій + підтримку.",
}
RU = {
    "short_description": "Kuma Shape — инновационный аппарат для коррекции фигуры с технологией многовекторной компрессии. Работает с локальными жировыми отложениями и тонусом кожи.",
    "addresses_problems": ["локальные жировые отложения на бёдрах, ягодицах, животе", "потеря тонуса кожи тела", "целлюлит средней и тяжёлой степени", "отёчность ног"],
    "target_audience": ["клиенты с локальными проблемными зонами", "перед сезоном открытой одежды", "после родов для восстановления"],
    "benefits": ["многовекторная компрессия для глубокого воздействия", "доступные зоны: бёдра (передняя/задняя/внутренняя/полностью), ягодицы, поясница, спина, ноги, руки (трицепс)", "результат с курса 8-10 сеансов", "сочетается с обёртыванием и массажем"],
    "keywords": ["kuma shape", "кума шейп", "коррекция фигуры", "целлюлит", "локальные жировые отложения"],
    "sales_pitch": "Kuma Shape — для точечной работы с проблемной зоной. Рекомендую курс 10-12 сеансов 2 раза в неделю. Поддержка раз в 2 недели после курса.",
    "cross_sell": ["обёртывание в комплексе", "антицеллюлитный массаж", "endospheres параллельно", "питьевой режим + активность"],
    "procedure_steps": ["консультация и выбор зоны", "переодевание в специальный костюм", "работа аппаратом по выбранной зоне", "завершающая релаксация"],
    "contraindications": ["беременность", "тромбофлебит", "онкология", "острые воспаления"],
    "aftercare_advice": "Питьевой режим 2-3 л воды. Физическая активность 30 мин в день. Рекомендую курс 10-12 сеансов + поддержку.",
}
EN = {
    "short_description": "Kuma Shape is an innovative body-sculpting device featuring multi-vector compression technology. It targets localised fat deposits and skin tone.",
    "addresses_problems": ["localised fat deposits on the thighs, buttocks and abdomen", "loss of skin tone on the body", "moderate to severe cellulite", "leg swelling"],
    "target_audience": ["clients with localised problem areas", "before the open-clothing season", "post-partum recovery"],
    "benefits": ["multi-vector compression for deep impact", "available zones: thighs (front / back / inner / full), buttocks, lower back, back, legs, arms (triceps)", "visible effect after a course of 8-10 sessions", "combines well with body wraps and massage"],
    "keywords": ["kuma shape", "body sculpting", "cellulite", "localised fat deposits", "multi-vector compression"],
    "sales_pitch": "Kuma Shape is designed for targeted work on a specific area. I recommend a course of 10-12 sessions twice a week, then maintenance every two weeks.",
    "cross_sell": ["body wraps in combination", "anti-cellulite massage", "Endospheres in parallel", "hydration and activity routine"],
    "procedure_steps": ["consultation and selection of the area", "changing into a special suit", "device work on the chosen zone", "final relaxation"],
    "contraindications": ["pregnancy", "thrombophlebitis", "oncology", "acute inflammation"],
    "aftercare_advice": "Drink 2-3 litres of water a day. Maintain 30 minutes of physical activity daily. I recommend a course of 10-12 sessions plus maintenance.",
}
PL = {
    "short_description": "Kuma Shape to innowacyjne urządzenie do modelowania sylwetki z technologią wielowektorowej kompresji. Działa na miejscowe nagromadzenia tkanki tłuszczowej i napięcie skóry.",
    "addresses_problems": ["miejscowe nagromadzenia tkanki tłuszczowej na udach, pośladkach i brzuchu", "utrata napięcia skóry ciała", "cellulit umiarkowanego i ciężkiego stopnia", "obrzęki nóg"],
    "target_audience": ["osoby z miejscowymi obszarami problemowymi", "przed sezonem letnim", "po porodzie dla regeneracji"],
    "benefits": ["wielowektorowa kompresja dla głębokiego oddziaływania", "dostępne strefy: uda (przód/tył/wewnętrzna/całość), pośladki, dolna część pleców, plecy, nogi, ramiona (triceps)", "widoczny efekt po serii 8-10 zabiegów", "dobrze łączy się z okładami i masażem"],
    "keywords": ["kuma shape", "modelowanie sylwetki", "cellulit", "miejscowe nagromadzenia tłuszczu", "kompresja wielowektorowa"],
    "sales_pitch": "Kuma Shape służy do precyzyjnej pracy nad konkretną strefą. Polecam Pani/Panu cykl 10-12 zabiegów dwa razy w tygodniu, a następnie podtrzymanie co dwa tygodnie.",
    "cross_sell": ["okłady w pakiecie", "masaż antycellulitowy", "Endospheres równolegle", "nawodnienie i aktywność"],
    "procedure_steps": ["konsultacja i wybór strefy", "przebranie się w specjalny strój", "praca urządzeniem nad wybraną strefą", "końcowa relaksacja"],
    "contraindications": ["ciąża", "zakrzepowe zapalenie żył", "choroby nowotworowe", "ostre stany zapalne"],
    "aftercare_advice": "Nawodnienie 2-3 litry wody dziennie. Aktywność fizyczna 30 minut dziennie. Polecam cykl 10-12 zabiegów oraz podtrzymanie.",
}
LANGS = {"uk": UK, "ru": RU, "en": EN, "pl": PL}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            # 1. Знайти existing Endospheres/Kuma keys
            for country in ("ua", "pl", "gb"):
                kr = await session.execute(text(f"""
                    SELECT DISTINCT canonical_key FROM {country}.service
                    WHERE archive=false AND (canonical_key ILIKE '%kuma%' OR name ILIKE '%kuma%')
                """))
                keys = sorted({row[0] for row in kr.all() if row[0]})
                if not keys:
                    print(f"  [{country}] no kuma keys — skip")
                    continue
                print(f"  [{country}] kuma keys: {len(keys)}")

                # Skip if Kuma Shape profile already exists
                exists = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND name='Kuma Shape'"
                ), {"c": country})
                if exists.scalar():
                    print(f"  ⚠ Kuma Shape already exists for {country} — skip create")
                    continue

                primary = keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                ), {"c": country, "k": primary})
                if chk.scalar():
                    primary = f"kuma_shape_{country}_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name="Kuma Shape",
                    country=country, default_language="uk", enabled=True,
                    created_by="kuma_v1", updated_by="kuma_v1",
                ))
                # Translations all 4 langs
                for lang, content in LANGS.items():
                    session.add(ServiceProfileTranslation(
                        profile_id=pid, language=lang, **content,
                    ))
                session.add(ServiceProfileOption(
                    profile_id=pid, option_type="family", name="Kuma Shape",
                    sort_order=0, canonical_keys=keys, **UK,
                ))
                await session.flush()
                print(f"  + created Kuma Shape [{country}] with {len(keys)} keys + 4 langs")

                # Remove these keys from Endospheres / Endosphere catch + Інші апаратні процедури тіла
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
                            print(f"  − removed {len(row[1] or []) - len(cleaned)} kuma keys from '{target_name}' [{country}]")

            await session.commit()

            # Re-link services
            for country in ("ua", "pl", "gb"):
                await session.execute(text(f"""
                    UPDATE {country}.service SET profile_id = NULL
                    WHERE archive=false AND (canonical_key ILIKE '%kuma%' OR name ILIKE '%kuma%')
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
