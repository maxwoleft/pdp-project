"""GB: створити profile "Ламінування вій" і "Ламінування брів" — забрати keys з інших profiles."""
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
        "name": "Ламінування вій",
        "keys": [
            "farbuvannya_laminuvannya_vii",
            "farbuvannya_vii",
            "laminuvannya_vii",
            "korektsiya_mizhviiky_napylennya_pudrovogo",
            "mizhviiky_napylennya_pudrove",
        ],
        "uk": {
            "short_description": "Послуги для вій — ламінування, фарбування, комплекс ламінування + фарбування, перманентний макіяж міжвійки.",
            "addresses_problems": [
                "тонкі прямі вії — хочеться вигину",
                "світлі вії потребують виразності",
                "набридло щодня фарбувати — постійне рішення міжвійки",
                "комплексний догляд вій"
            ],
            "target_audience": [
                "клієнтки що хочуть м'якого natural ефекту",
                "перед подією для виразного погляду",
                "регулярний догляд раз на 4-6 тижнів"
            ],
            "benefits": [
                "ламінування дає вигин і виразність на 4-6 тижнів",
                "фарбування додає темного відтінку",
                "комплекс ламінування + фарбування — максимум ефекту",
                "перманентний макіяж міжвійки — постійний підвід"
            ],
            "keywords": ["ламінування вій", "фарбування вій", "eyelash lamination", "lash tint", "lash line permanent", "перманентний макіяж міжвійки"],
            "sales_pitch": "Раджу комплекс ламінування + фарбування — максимум ефекту за один візит на 4-6 тижнів. Якщо хочеться постійно — перманентний макіяж міжвійки.",
            "cross_sell": ["ламінування брів того ж візиту", "регулярний візит раз на 4-6 тижнів"],
            "procedure_steps": ["діагностика стану вій", "очищення", "нанесення складу", "витримка", "фарбування за бажанням", "догляд"],
            "contraindications": ["алергія на компоненти", "запалення очей"],
            "aftercare_advice": "Перші 24 години не мочити. Маска / олія для вій вдома. Раджу повторювати раз на 4-6 тижнів."
        }
    },
    {
        "name": "Ламінування брів",
        "keys": [
            "briv_dlya_shchastya",
            "briv_farbuvannya",
            "briv_farbuvannya_korektsiya",
            "briv_farbuvannya_laminuvannya",
            "briv_forma",
            "briv_korektsiya",
            "briv_laminuvannya",
            "briv_modelyuvannya",
            "briv_korektsiya_napylennya_pudrovogo",
            "briv_napylennya_pudrove",
            "briv_vysvitlennya",
        ],
        "uk": {
            "short_description": "Послуги для брів — ламінування, фарбування, корекція, моделювання, висвітлення, перманентний макіяж пудрове напилення (Powder/Ombre Brows). Комплекс щастя для брів.",
            "addresses_problems": [
                "неохайна форма брів",
                "редкі брови потребують фарбування",
                "хочеться стабільної форми надовго (пудрове напилення)",
                "розросте у різні боки — потрібен fixation",
                "світлі брови — потрібна виразність"
            ],
            "target_audience": [
                "клієнтки на регулярному догляді (раз на 4-6 тижнів)",
                "перед подією / зйомкою",
                "хто шукає перманентне рішення (Powder Brows)"
            ],
            "benefits": [
                "ламінування фіксує форму на 4-6 тижнів",
                "фарбування + корекція в одному візиті",
                "Powder/Ombre Brows — перманент 1-2 роки",
                "комплекс 'Щастя для брів' — повний догляд",
                "доступне висвітлення брів"
            ],
            "keywords": ["ламінування брів", "фарбування брів", "powder brows", "ombre brows", "перманентний макіяж брів", "щастя для брів", "корекція брів", "моделювання брів", "висвітлення брів"],
            "sales_pitch": "Раджу комплекс ламінування + фарбування + корекція — повний догляд за один візит на 4-6 тижнів. Якщо постійно — Powder Brows перманент.",
            "cross_sell": ["ламінування вій того ж візиту", "регулярний візит раз на 4-6 тижнів", "консультація перед Powder Brows"],
            "procedure_steps": ["діагностика", "корекція форми (пінцет / нитка / віск)", "ламінування або фарбування за бажанням", "догляд"],
            "contraindications": ["алергія", "активні запалення у зоні"],
            "aftercare_advice": "Перші 24 години не мочити. Бережіть від саун. Раджу повторювати раз на 4-6 тижнів."
        }
    },
]


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

                kr = await session.execute(text(
                    "SELECT DISTINCT canonical_key FROM gb.service WHERE archive=false AND canonical_key = ANY(:k)"
                ), {"k": prof["keys"]})
                real_keys = sorted({row[0] for row in kr.all() if row[0]})
                if not real_keys:
                    print(f"  ⚠ {prof['name']} GB: no real keys")
                    continue

                primary = real_keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
                ), {"k": primary})
                if chk.scalar():
                    primary = f"{prof['name'].lower().replace(' ', '_')}_gb_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name=prof["name"],
                    country="gb", default_language="uk", enabled=True,
                    created_by="gb_lashbrow_v1", updated_by="gb_lashbrow_v1",
                    canonical_keys=real_keys,
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **prof["uk"],
                ))
                await session.flush()
                print(f"  + created {prof['name']} GB ({len(real_keys)} keys)")
                all_keys.extend(real_keys)

            # Remove ці keys з УСІХ GB profiles (крім нових)
            r = await session.execute(text(
                "SELECT id, name, canonical_keys FROM service_profile WHERE country='gb'"
            ))
            for row in r.all():
                if row[1] in ("Ламінування вій", "Ламінування брів"):
                    continue
                old = list(row[2] or [])
                cleaned = [k for k in old if k not in all_keys]
                if len(cleaned) != len(old):
                    await session.execute(text(
                        "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                    ), {"k": json.dumps(cleaned), "id": row[0]})
                    print(f"  − removed {len(old) - len(cleaned)} keys з '{row[1]}' GB")

            await session.commit()

            # Re-link
            await session.execute(text(
                "UPDATE gb.service SET profile_id=NULL WHERE archive=false AND canonical_key = ANY(:k)"
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
            print(f"  ↻ re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
