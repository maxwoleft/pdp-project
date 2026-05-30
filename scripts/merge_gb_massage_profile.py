"""Merge 6 GB масажних profiles → 1 "Масаж". Перенести masazh_nig зі SPA-стоп.

Зробити:
1. Витягнути masazh_nig зі SPA-догляд для стоп GB
2. Створити новий profile "Масаж" GB з UNION усіх canonical_keys + key_descriptions
3. Translation (uk) inline-написано
4. Видалити 6 source profiles
5. Re-link service.profile_id
6. compute_profile_salons
"""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


SOURCE_NAMES = [
    "Антицелюлітний масаж",
    "Інші види масажу",
    "Класичний масаж",
    "Лімфодренажний масаж",
    "Локальний масаж (голова, шия, спина, обличчя)",
    "Спортивний масаж",
]

UK = {
    "short_description": "Масаж — повна лінійка процедур: класичний, релакс, антицелюлітний, лімфодренажний, спортивний, локальні зони (голова, шия, спина, обличчя, стопи).",
    "addresses_problems": [
        "напруга в мʼязах після робочого тижня",
        "хронічна напруга у спині та шиї (офісна робота)",
        "целюліт і набряклість ніг",
        "стрес і потреба у глибокому розслабленні",
        "м'язи скуті після тренувань",
        "втома і набряк обличчя"
    ],
    "target_audience": [
        "клієнти з офісною роботою на регулярному догляді",
        "спортсмени для відновлення",
        "перед подією для розслаблення",
        "клієнти з целюлітом 1-3 ступеня"
    ],
    "benefits": [
        "класичний для базового регулярного догляду",
        "антицелюлітний з лімфодренажним ефектом курсом 8-10",
        "лімфодренажний м'яка техніка проти набряків",
        "спортивний deep tissue для м'язового відновлення",
        "локальні зони (голова, шия, спина, обличчя, стопи) точково 20-40 хв",
        "тайський, медовий, GOLD як спеціальні техніки"
    ],
    "keywords": [
        "масаж", "класичний масаж", "антицелюлітний", "лімфодренажний",
        "спортивний масаж", "релакс", "масаж спини", "масаж шиї",
        "тайський масаж", "медовий масаж"
    ],
    "sales_pitch": "Раджу спершу визначити мету: класичний для розслаблення, антицелюлітний курсом для корекції, лімфодренажний при набряках, спортивний для активних. Локальний за конкретною зоною.",
    "cross_sell": [
        "обгортання у комплексі для антицелюліту",
        "SPA-догляд для рук/стоп паралельно",
        "регулярний візит раз на 2-4 тижні"
    ],
    "procedure_steps": [
        "консультація з підбором техніки",
        "підготовка зони",
        "робота за обраною технікою",
        "завершальна релаксація"
    ],
    "contraindications": [
        "гострі запалення",
        "висока температура",
        "варикоз у стадії (для антицелюлітного)",
        "вагітність (для антицелюлітного і деяких технік)",
        "тромбофлебіт",
        "онкологія"
    ],
    "aftercare_advice": "Питний режим 2-3 л води. Уникайте важкої їжі 2 години. При антицелюлітному курсі — помірна фізична активність 30 хв щодня.",
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            # 1. Find SPA-догляд для стоп GB — extract masazh_nig
            spa_row = await session.execute(text(
                "SELECT id, canonical_keys, key_descriptions FROM service_profile "
                "WHERE country='gb' AND name='SPA-догляд для стоп'"
            ))
            spa = spa_row.first()
            if spa:
                spa_keys = list(spa[1] or [])
                spa_descs = dict(spa[2] or {})
                if "masazh_nig" in spa_keys:
                    spa_keys.remove("masazh_nig")
                    nig_desc = spa_descs.pop("masazh_nig", "")
                    await session.execute(text("""
                        UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb),
                          key_descriptions=CAST(:d AS jsonb) WHERE id=:pid
                    """), {"k": json.dumps(spa_keys), "d": json.dumps(spa_descs), "pid": spa[0]})
                    print(f"  − removed masazh_nig from SPA-догляд для стоп GB")
                else:
                    nig_desc = ""

            # 2. Collect все з 6 source profiles
            all_keys: list[str] = ["masazh_nig"]
            all_descs: dict[str, str] = {}
            if nig_desc:
                all_descs["masazh_nig"] = nig_desc
            source_ids: list[str] = []

            for name in SOURCE_NAMES:
                r = await session.execute(text(
                    "SELECT id, canonical_key, canonical_keys, key_descriptions "
                    "FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": name})
                row = r.first()
                if not row:
                    print(f"  ⚠ Not found: {name}")
                    continue
                source_ids.append(row[0])
                if row[1] and row[1] not in all_keys:
                    all_keys.append(row[1])
                for k in (row[2] or []):
                    if k not in all_keys:
                        all_keys.append(k)
                for k, v in (row[3] or {}).items():
                    if k not in all_descs and v:
                        all_descs[k] = v
                print(f"  + collected from {name}")

            # 3. Створити новий profile "Масаж" GB
            primary = all_keys[0] if all_keys else f"masazh_gb_{uuid.uuid4().hex[:8]}"
            chk = await session.execute(text(
                "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
            ), {"k": primary})
            if chk.scalar():
                primary = f"masazh_gb_{uuid.uuid4().hex[:8]}"

            new_pid = str(uuid.uuid4())
            session.add(ServiceProfile(
                id=new_pid, canonical_key=primary, name="Масаж",
                country="gb", default_language="uk", enabled=True,
                created_by="merge_v1", updated_by="merge_v1",
                canonical_keys=all_keys, key_descriptions=all_descs,
            ))
            session.add(ServiceProfileTranslation(
                profile_id=new_pid, language="uk", **UK,
            ))
            await session.flush()
            print(f"  + created Масаж GB with {len(all_keys)} keys, {len(all_descs)} descriptions")

            # 4. Delete source profiles
            if source_ids:
                d = await session.execute(text(
                    "DELETE FROM service_profile WHERE id = ANY(:ids)"
                ), {"ids": source_ids})
                print(f"  ✖ deleted {d.rowcount} source profiles")
            await session.commit()

            # 5. Re-link gb.service.profile_id для усіх керованих keys
            keys_to_relink = all_keys + ["masazh_nig"]
            await session.execute(text("""
                UPDATE gb.service SET profile_id = NULL
                WHERE archive=false AND canonical_key = ANY(:k)
            """), {"k": keys_to_relink})
            r = await session.execute(text("""
                UPDATE gb.service s SET profile_id = sub.profile_id
                FROM (
                  SELECT DISTINCT ON (canonical_key) canonical_key, id AS profile_id
                  FROM (
                    SELECT jsonb_array_elements_text(p.canonical_keys) AS canonical_key, p.id
                    FROM public.service_profile p
                    WHERE p.country = 'gb'
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
