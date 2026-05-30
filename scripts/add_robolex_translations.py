"""Додає RU/EN/PL translations для UA profile 'Robolex'. Без LLM — Claude-written.
"""
import asyncio
import json

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


TRANSLATIONS = {
    "ru": {
        "short_description": "Robolex — инновационный аппарат робот-массажа для коррекции фигуры и ухода за лицом. Работает методом биомеханической стимуляции.",
        "addresses_problems": ["локальные жировые отложения на лице или теле", "застойная лимфа и отёчность", "потеря тонуса кожи", "возрастные изменения овала лица"],
        "target_audience": ["клиенты, желающие коррекции фигуры неинвазивно", "перед сезоном или событием", "клиенты 30+ для поддержания тонуса кожи"],
        "benefits": ["робот-массаж с заданной точностью", "одновременное воздействие на жировую ткань, лимфу и мышцы", "комфортная процедура без боли", "доступен для лица, шеи, декольте и тела"],
        "keywords": ["robolex", "роболекс", "робот массаж", "коррекция фигуры", "робот для лица"],
        "sales_pitch": "Robolex сочетает точность роботизированной системы с эффектом мануальной техники. Рекомендую курс 8-10 процедур раз в неделю для выраженного результата.",
        "cross_sell": ["обёртывание в комплексе", "endospheres параллельно", "консультация для подбора протокола"],
        "procedure_steps": ["консультация и диагностика зоны", "переодевание", "работа аппаратом по зонам", "завершающий уход"],
        "contraindications": ["беременность", "онкология", "острые воспаления в зоне работы"],
        "aftercare_advice": "Питьевой режим 2 л воды. Умеренная физическая активность. Рекомендую курсом 8-10 процедур.",
    },
    "en": {
        "short_description": "Robolex is an innovative robotic massage device for body sculpting and facial care. It uses biomechanical stimulation.",
        "addresses_problems": ["localised fat deposits on the face or body", "lymphatic congestion and puffiness", "loss of skin tone", "age-related changes to the facial contour"],
        "target_audience": ["clients seeking non-invasive body sculpting", "before a season or a special event", "clients 30+ wishing to maintain skin tone"],
        "benefits": ["robotic massage with precise targeting", "simultaneous action on fat tissue, the lymphatic system and muscles", "comfortable, pain-free procedure", "available for the face, neck, décolleté and body"],
        "keywords": ["robolex", "robotic massage", "body sculpting", "robotic facial", "non-invasive body contouring"],
        "sales_pitch": "Robolex combines the precision of a robotic system with the effect of a manual technique. I recommend a course of 8–10 sessions, once a week, for a noticeable result.",
        "cross_sell": ["body wraps in combination", "Endospheres in parallel", "consultation to choose the right protocol"],
        "procedure_steps": ["consultation and assessment of the treatment area", "changing into a treatment garment", "device work across the target zones", "post-treatment care"],
        "contraindications": ["pregnancy", "oncology", "acute inflammation in the treatment area"],
        "aftercare_advice": "Drink at least 2 litres of water a day. Maintain moderate physical activity. I recommend a course of 8–10 sessions.",
    },
    "pl": {
        "short_description": "Robolex to innowacyjne urządzenie do masażu robotycznego, służące do modelowania sylwetki i pielęgnacji twarzy. Działa metodą stymulacji biomechanicznej.",
        "addresses_problems": ["miejscowe nagromadzenia tkanki tłuszczowej na twarzy lub ciele", "zastoinowy układ limfatyczny i obrzęki", "utrata napięcia skóry", "zmiany związane z wiekiem w obrębie owalu twarzy"],
        "target_audience": ["osoby pragnące nieinwazyjnej korekty sylwetki", "przed sezonem lub ważnym wydarzeniem", "klienci 30+ chcący utrzymać napięcie skóry"],
        "benefits": ["masaż robotyczny o wysokiej precyzji", "jednoczesny wpływ na tkankę tłuszczową, limfę i mięśnie", "komfortowy zabieg bez bólu", "dostępny dla twarzy, szyi, dekoltu i ciała"],
        "keywords": ["robolex", "masaż robotyczny", "modelowanie sylwetki", "robot do twarzy", "nieinwazyjna korekta"],
        "sales_pitch": "Robolex łączy precyzję systemu robotycznego z efektem techniki manualnej. Polecam Pani/Panu cykl 8-10 zabiegów raz w tygodniu dla wyraźnego rezultatu.",
        "cross_sell": ["okłady w pakiecie", "Endospheres równolegle", "konsultacja w celu doboru protokołu"],
        "procedure_steps": ["konsultacja i ocena obszaru zabiegu", "przebranie się", "praca urządzeniem na poszczególnych strefach", "pielęgnacja końcowa"],
        "contraindications": ["ciąża", "choroby nowotworowe", "ostre stany zapalne w miejscu zabiegu"],
        "aftercare_advice": "Nawodnienie 2 litry wody dziennie. Umiarkowana aktywność fizyczna. Polecam cykl 8-10 zabiegów.",
    },
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            r = await session.execute(text(
                "SELECT id FROM public.service_profile WHERE country='ua' AND name='Robolex'"
            ))
            pid = r.scalar()
            if not pid:
                print("Robolex UA profile not found")
                return

            for lang, content in TRANSLATIONS.items():
                exists = await session.execute(text(
                    "SELECT id FROM public.service_profile_translation WHERE profile_id=:p AND language=:l"
                ), {"p": str(pid), "l": lang})
                if exists.scalar():
                    print(f"  ⚠ {lang} translation already exists — skip")
                    continue
                await session.execute(text("""
                    INSERT INTO public.service_profile_translation
                      (id, profile_id, language, short_description,
                       addresses_problems, target_audience, benefits, keywords,
                       sales_pitch, cross_sell, procedure_steps, contraindications,
                       aftercare_advice, created_at, updated_at)
                    VALUES
                      (gen_random_uuid(), :p, :l, :sd,
                       CAST(:ap AS jsonb), CAST(:ta AS jsonb), CAST(:bn AS jsonb), CAST(:kw AS jsonb),
                       :sp, CAST(:cs AS jsonb), CAST(:ps AS jsonb), CAST(:ci AS jsonb),
                       :aa, NOW(), NOW())
                """), {
                    "p": str(pid), "l": lang,
                    "sd": content["short_description"],
                    "ap": json.dumps(content["addresses_problems"]),
                    "ta": json.dumps(content["target_audience"]),
                    "bn": json.dumps(content["benefits"]),
                    "kw": json.dumps(content["keywords"]),
                    "sp": content["sales_pitch"],
                    "cs": json.dumps(content["cross_sell"]),
                    "ps": json.dumps(content["procedure_steps"]),
                    "ci": json.dumps(content["contraindications"]),
                    "aa": content["aftercare_advice"],
                })
                print(f"  + inserted {lang} translation for Robolex UA")

            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
