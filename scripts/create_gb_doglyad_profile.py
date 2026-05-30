"""GB: створити profile 'Догляд за волоссям' з 6 doglyad keys."""
import asyncio
import json
import uuid

from sqlalchemy import text
from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


KEYS = [
    "color_do_doglyad_dovzhyna_gloss_kupryka",
    "color_doglyad_gloss",
    "color_doglyad_gloss_seredne_volossya",
    "doglyad_k18_sushka",
    "doglyad_serednye_sushka_volossya",
    "doglyad_sushka",
]

UK = {
    "short_description": "Догляд за волоссям — брендові процедури відновлення і фіксації кольору. Brae Bond Angel, Brae Power Dose, К18, Redken color gloss. Включена сушка.",
    "addresses_problems": [
        "пошкоджене волосся після фарбування або освітлення",
        "тьмяний колір потребує освіження",
        "потрібен глибокий відновлювальний догляд між фарбуваннями",
        "захист волосся під час хімічних процедур"
    ],
    "target_audience": [
        "клієнтки після мелірування / фарбування",
        "пошкоджене волосся потребує курсу",
        "регулярна підтримка раз на 2-3 тижні"
    ],
    "benefits": [
        "доступні бренди: Brae Bond Angel, Brae Power Dose, К18, Redken color gloss",
        "глибоке відновлення зсередини",
        "освіження кольору без переробки",
        "включена професійна сушка"
    ],
    "keywords": ["догляд за волоссям", "brae bond angel", "k18", "redken color gloss", "brae power dose", "відновлення волосся"],
    "sales_pitch": "Для пошкодженого волосся раджу курс К18 або Brae Bond Angel — глибоке відновлення. Для оновлення кольору — Redken color gloss. Раджу раз на 2-3 тижні.",
    "cross_sell": [
        "регулярний візит у курсі",
        "домашній шампунь без сульфатів",
        "процедура під час фарбування для захисту"
    ],
    "procedure_steps": [
        "діагностика стану волосся",
        "очищення",
        "нанесення обраного бренду",
        "витримка згідно протоколу",
        "змивання",
        "сушка"
    ],
    "contraindications": [],
    "aftercare_advice": "Шампунь без сульфатів. Маска вдома раз на тиждень. Курсом 3-4 процедури з інтервалом 2 тижні для виразного результату."
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            ex = await session.execute(text(
                "SELECT id FROM service_profile WHERE country='gb' AND name='Догляд за волоссям'"
            ))
            if ex.scalar():
                print("  ⚠ Догляд за волоссям GB already exists — skip create")
                return

            kr = await session.execute(text(
                "SELECT DISTINCT canonical_key FROM gb.service WHERE archive=false AND canonical_key = ANY(:k)"
            ), {"k": KEYS})
            real_keys = sorted({row[0] for row in kr.all() if row[0]})
            if not real_keys:
                print("  ⚠ no real keys — skip")
                return

            primary = real_keys[0]
            chk = await session.execute(text(
                "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
            ), {"k": primary})
            if chk.scalar():
                primary = f"doglyad_volossya_gb_{uuid.uuid4().hex[:8]}"

            pid = str(uuid.uuid4())
            session.add(ServiceProfile(
                id=pid, canonical_key=primary, name="Догляд за волоссям",
                country="gb", default_language="uk", enabled=True,
                created_by="gb_doglyad_v1", updated_by="gb_doglyad_v1",
                canonical_keys=real_keys,
            ))
            session.add(ServiceProfileTranslation(
                profile_id=pid, language="uk", **UK,
            ))
            await session.flush()
            print(f"  + created Догляд за волоссям GB with {len(real_keys)} keys")

            # Remove з "Інші процедури для волосся"
            r = await session.execute(text(
                "SELECT id, canonical_keys FROM service_profile WHERE country='gb' AND name='Інші процедури для волосся'"
            ))
            row = r.first()
            if row:
                cleaned = [k for k in (row[1] or []) if k not in real_keys]
                await session.execute(text(
                    "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                ), {"k": json.dumps(cleaned), "id": row[0]})
                print(f"  − removed {len(row[1] or []) - len(cleaned)} keys з 'Інші процедури для волосся'")
            await session.commit()

            await session.execute(text(
                "UPDATE gb.service SET profile_id=NULL WHERE archive=false AND canonical_key = ANY(:k)"
            ), {"k": real_keys})
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
