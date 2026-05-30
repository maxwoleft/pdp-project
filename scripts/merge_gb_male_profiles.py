"""GB: merge 8 чоловічих profiles → 1 'Чоловічі послуги'."""
import asyncio
import json
import uuid

from sqlalchemy import text
from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


SOURCE_NAMES = [
    "Борода та вуса",
    "Корекція брів чоловіча",
    "Чоловіча стрижка",
    "Чоловіча укладка",
    "Чоловіче фарбування / Камуфляж",
    "Чоловічий манікюр",
    "Чоловічий педикюр",
    "Інші процедури для чоловіків",
]

UK = {
    "short_description": "Чоловічі послуги салону — повна лінійка: стрижка машинкою / ножицями, борода і вуса, укладка, фарбування / камуфляж, манікюр і педикюр (класичний, медичний), корекція брів.",
    "addresses_problems": [
        "відросла стрижка чи борода",
        "сива щетина — потрібен камуфляж",
        "ділова презентація — потрібен охайний вигляд",
        "регулярний догляд раз на 3-4 тижні",
        "руки/нігті потребують доглянутого вигляду",
        "стопа — медичний педикюр при проблемах"
    ],
    "target_audience": [
        "регулярні клієнти на підтриманні форми",
        "ділові чоловіки",
        "клієнти з бородою на постійному догляді",
        "перед подією або зйомкою"
    ],
    "benefits": [
        "майстри-барбери з досвідом",
        "повний комплекс чоловічих послуг в одному місці",
        "стрижка + борода + догляд за нігтями за один візит",
        "доступні класичні, японські, SPA і медичні варіанти манікюру/педикюру",
        "камуфляж сивини природний результат"
    ],
    "keywords": [
        "чоловіча стрижка", "стрижка чоловіча", "men haircut",
        "борода", "вуса", "beard", "barber",
        "чоловічий манікюр", "men manicure",
        "чоловічий педикюр", "men pedicure",
        "чоловіче фарбування", "камуфляж сивини",
        "корекція брів чоловіча"
    ],
    "sales_pitch": "Раджу комплекс: стрижка + борода + догляд за нігтями за один візит. Регулярно раз на 3-4 тижні утримує форму. При сивині — камуфляж природний результат.",
    "cross_sell": [
        "комплексний візит стрижка + борода",
        "регулярний візит раз на 3-4 тижні",
        "догляд для жорсткого волосся вдома"
    ],
    "procedure_steps": [
        "консультація з обраної послугою",
        "виконання (стрижка / борода / манікюр / педикюр)",
        "фінальний догляд і стайлінг"
    ],
    "contraindications": [],
    "aftercare_advice": "Регулярний візит раз на 3-4 тижні. Для бороди — олія вдома. Для нігтів — олія для кутикули."
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            # Collect
            all_keys: list[str] = []
            all_descs: dict[str, str] = {}
            source_ids: list[str] = []
            for name in SOURCE_NAMES:
                r = await session.execute(text(
                    "SELECT id, canonical_keys, key_descriptions FROM service_profile "
                    "WHERE country='gb' AND name=:n"
                ), {"n": name})
                row = r.first()
                if not row:
                    print(f"  ⚠ {name} GB not found")
                    continue
                source_ids.append(row[0])
                for k in (row[1] or []):
                    if k not in all_keys:
                        all_keys.append(k)
                for k, v in (row[2] or {}).items():
                    if k not in all_descs and v:
                        all_descs[k] = v
                print(f"  + collected from {name} ({len(row[1] or [])} keys)")

            # Create merged profile
            primary = all_keys[0] if all_keys else f"choloviki_gb_{uuid.uuid4().hex[:8]}"
            chk = await session.execute(text(
                "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
            ), {"k": primary})
            if chk.scalar():
                primary = f"choloviki_gb_{uuid.uuid4().hex[:8]}"

            new_pid = str(uuid.uuid4())
            session.add(ServiceProfile(
                id=new_pid, canonical_key=primary, name="Чоловічі послуги",
                country="gb", default_language="uk", enabled=True,
                created_by="gb_male_merge_v1", updated_by="gb_male_merge_v1",
                canonical_keys=all_keys, key_descriptions=all_descs,
            ))
            session.add(ServiceProfileTranslation(
                profile_id=new_pid, language="uk", **UK,
            ))
            await session.flush()
            print(f"  + created Чоловічі послуги GB ({len(all_keys)} keys, {len(all_descs)} descs)")

            # Delete sources
            if source_ids:
                d = await session.execute(text(
                    "DELETE FROM service_profile WHERE id = ANY(:ids)"
                ), {"ids": source_ids})
                print(f"  ✖ deleted {d.rowcount} source profiles")
            await session.commit()

            # Re-link gb.service
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
            print(f"  ↻ gb re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
