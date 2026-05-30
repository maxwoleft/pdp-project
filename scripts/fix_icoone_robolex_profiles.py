"""Виправлення:
1. Перенести 'laser_masazh_oblychchya' з 'Інші апаратні процедури тіла' → 'Icoone Laser масаж'
2. Створити окремий profile 'Robolex' (UA/PL/GB) + перенести robolex keys

Re-link service.profile_id після зміни canonical_keys[].
"""
from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


ROBOLEX_CONTENT = {
    "short_description": "Robolex — інноваційний апарат робот-масажу для корекції фігури і догляду за обличчям. Працює методом біомеханічної стимуляції.",
    "addresses_problems": [
        "локальні жирові відкладення на обличчі або тілі",
        "застійна лімфа і набряклість",
        "втрата тонусу шкіри",
        "вікові зміни овалу обличчя",
    ],
    "target_audience": [
        "клієнти що хочуть корекції фігури неінвазивно",
        "перед сезоном або подією",
        "клієнти 30+ для підтримки тонусу шкіри",
    ],
    "benefits": [
        "робот-масаж із заданою точністю",
        "одночасний вплив на жирову тканину, лімфу та м'язи",
        "комфортна процедура без болю",
        "доступний для обличчя, шиї, декольте і тіла",
    ],
    "keywords": ["robolex", "роболекс", "робот масаж", "корекція фігури", "робот для обличчя"],
    "sales_pitch": "Robolex поєднує точність роботизованої системи з ефектом мануальної техніки. Раджу курс 8-10 процедур раз на тиждень для виразного результату.",
    "cross_sell": ["обгортання у комплексі", "endospheres паралельно", "консультація для підбору протоколу"],
    "procedure_steps": ["консультація і діагностика зони", "переодягання", "робота апаратом по зонах", "завершальний догляд"],
    "contraindications": ["вагітність", "онкологія", "гострі запалення у зоні роботи"],
    "aftercare_advice": "Питний режим 2 л води. Помірна фізична активність. Раджу курсом 8-10 процедур.",
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # ── Step 1: Icoone fix ─────────────────────────────────
            ico_pl = await session.execute(text(
                "SELECT id FROM public.service_profile WHERE country='ua' AND name='Icoone Laser масаж'"
            ))
            ico_pid = ico_pl.scalar()
            cat_pl = await session.execute(text(
                "SELECT id FROM public.service_profile WHERE country='ua' AND name='Інші апаратні процедури тіла'"
            ))
            cat_pid = cat_pl.scalar()

            if ico_pid and cat_pid:
                # Add to Icoone family option
                ico_opt = await session.execute(text(
                    "SELECT id, canonical_keys FROM public.service_profile_option WHERE profile_id=:p AND option_type='family'"
                ), {"p": str(ico_pid)})
                row = ico_opt.first()
                if row:
                    keys = list(row[1] or [])
                    if "laser_masazh_oblychchya" not in keys:
                        keys.append("laser_masazh_oblychchya")
                        await session.execute(text(
                            "UPDATE public.service_profile_option SET canonical_keys=CAST(:k AS jsonb), embedding=NULL WHERE id=:id"
                        ), {"k": json.dumps(keys), "id": row[0]})
                        print(f"  + added 'laser_masazh_oblychchya' to Icoone Laser масаж UA")

                # Remove from catch-all family option
                cat_opt = await session.execute(text(
                    "SELECT id, canonical_keys FROM public.service_profile_option WHERE profile_id=:p AND option_type='family'"
                ), {"p": str(cat_pid)})
                row = cat_opt.first()
                if row:
                    keys = [k for k in (row[1] or []) if k != "laser_masazh_oblychchya"]
                    await session.execute(text(
                        "UPDATE public.service_profile_option SET canonical_keys=CAST(:k AS jsonb), embedding=NULL WHERE id=:id"
                    ), {"k": json.dumps(keys), "id": row[0]})
                    print(f"  − removed 'laser_masazh_oblychchya' from catch-all UA")

            # ── Step 2: Create Robolex profile ─────────────────────
            robolex_keys_by_country: dict[str, list[str]] = {}
            for country in ("ua", "pl", "gb"):
                r = await session.execute(text(f"""
                    SELECT DISTINCT canonical_key FROM {country}.service
                    WHERE archive=false AND (canonical_key ILIKE '%robolex%' OR name ILIKE '%robolex%')
                """))
                keys = sorted({row[0] for row in r.all() if row[0]})
                if keys:
                    robolex_keys_by_country[country] = keys
                    print(f"  [{country}] Robolex keys: {keys}")

            for country, keys in robolex_keys_by_country.items():
                # Skip if Robolex profile already exists for this country
                exists = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND name='Robolex'"
                ), {"c": country})
                if exists.scalar():
                    print(f"  ⚠ Robolex already exists for {country} — skip create")
                    continue

                primary = keys[0]
                # Check collision
                chk = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                ), {"c": country, "k": primary})
                if chk.scalar():
                    primary = f"robolex_{country}_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name="Robolex",
                    country=country, default_language="uk", enabled=True,
                    created_by="robolex_v1", updated_by="robolex_v1",
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **ROBOLEX_CONTENT,
                ))
                session.add(ServiceProfileOption(
                    profile_id=pid, option_type="family", name="Robolex",
                    sort_order=0, canonical_keys=keys, **ROBOLEX_CONTENT,
                ))
                await session.flush()
                print(f"  + created Robolex [{country}] with {len(keys)} keys")

                # Remove these keys from catch-all of same country
                cat = await session.execute(text(
                    "SELECT o.id, o.canonical_keys FROM public.service_profile_option o "
                    "JOIN public.service_profile p ON p.id=o.profile_id "
                    "WHERE p.country=:c AND p.name='Інші апаратні процедури тіла' AND o.option_type='family'"
                ), {"c": country})
                row = cat.first()
                if row:
                    cleaned = [k for k in (row[1] or []) if k not in keys]
                    await session.execute(text(
                        "UPDATE public.service_profile_option SET canonical_keys=CAST(:k AS jsonb), embedding=NULL WHERE id=:id"
                    ), {"k": json.dumps(cleaned), "id": row[0]})
                    print(f"  − removed {len(keys)} robolex keys from catch-all [{country}]")

            await session.commit()
            print("\nProfiles fixed.")

            # ── Step 3: Re-link affected services ──────────────────
            for country in ("ua", "pl", "gb"):
                # Force re-link by setting profile_id to NULL for relevant keys, then backfill
                await session.execute(text(f"""
                    UPDATE {country}.service SET profile_id = NULL
                    WHERE archive=false
                      AND canonical_key IN ('laser_masazh_oblychchya', 'robolex', 'oblychchya_robolex', 'dekolte_oblychchya_robolex_shyya')
                """))
                r = await session.execute(text(f"""
                    UPDATE {country}.service s
                    SET profile_id = sub.profile_id
                    FROM (
                      SELECT DISTINCT ON (canonical_key) canonical_key, profile_id
                      FROM (
                        SELECT jsonb_array_elements_text(o.canonical_keys) AS canonical_key, o.profile_id
                        FROM public.service_profile_option o
                        JOIN public.service_profile p ON p.id = o.profile_id
                        WHERE p.country = :c
                      ) x
                      ORDER BY canonical_key, profile_id
                    ) sub
                    WHERE s.canonical_key = sub.canonical_key
                      AND s.archive = false
                      AND s.profile_id IS NULL
                """), {"c": country})
                print(f"  [{country}] re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
