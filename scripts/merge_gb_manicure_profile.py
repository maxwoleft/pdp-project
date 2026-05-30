"""GB: merge всіх manicure profiles → 1 'Манікюр'. Чоловічий залишається у Чоловічі послуги.
Protезування, SPA-догляд для ніг — окремо.
"""
import asyncio
import json
import uuid

from sqlalchemy import text
from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


MANICURE_KEYS = [
    "manikyur",
    "manikyur_medychnyi",
    "manikyur_yaponskyi",
    "10_6_gel_long_manikyur_mm_nigti_ukrainskyi",
    "10_extra_gel_long_manikyur_mm_nigti_ukrainskyi_vid",
    "5_do_gel_manikyur_medium_mm_nigti_short_ukrainskyi",
    "french_gel_manikyur_pokryttya_ukrainskyi",
    "french_manikyur_medychnyi_pokryttya_znyattya",
    "gel_lakom_manikyur_medychnyi_pokryttya_znyattya",
    "gel_manikyur_ombre_pokryttya_ukrainskyi",
    "lakom_manikyur_medychnyi_zi_znyattya_zvychainym",
    "lakom_manikyur_pokryttya_znyattya_zvychainym",
]

DELETE_PROFILES = ["Манікюр класичний", "Манікюр японський"]

UK = {
    "short_description": "Манікюр — повна лінійка: класичний, японський, медичний, гель-лак, український гель-манікюр (Short/Medium/Long/Extra Long), френч, ombre, зняття звичайного лаку та гель-лаку.",
    "addresses_problems": [
        "відросли нігті — потрібна корекція форми",
        "хочеться тривалого покриття (гель-лак на 3-4 тижні)",
        "проблемна шкіра кутикули — медичний догляд",
        "хочеться стилю — френч / ombre / японський",
        "ламкі м'які нігті — український гель"
    ],
    "target_audience": [
        "регулярні клієнтки на 3-4 тижні",
        "перед подією / зйомкою",
        "ті, у кого проблемна кутикула — медичний"
    ],
    "benefits": [
        "повна лінійка покриттів і технік за один візит",
        "доступні варіанти за довжиною нігтя (до 5/6-10/від 10 мм)",
        "український гель-манікюр як зміцнення + покриття",
        "медичний — для проблемної кутикули",
        "японський — натуральний догляд з зміцненням"
    ],
    "keywords": [
        "манікюр", "manicure", "гель-лак", "gel polish", "український гель манікюр",
        "ukrainian gel manicure", "медичний манікюр", "medical manicure",
        "японський манікюр", "japanese manicure", "френч манікюр", "french",
        "ombre nails", "зняття гель-лаку"
    ],
    "sales_pitch": "Якщо хочете тривалого покриття — гель-лак чи український гель-манікюр на 3-4 тижні. Класичний / японський — для регулярного догляду без покриття. Раджу подивитись на стан кутикули перед першим візитом.",
    "cross_sell": [
        "педикюр того ж візиту",
        "SPA-догляд для рук паралельно",
        "регулярний візит раз на 3-4 тижні"
    ],
    "procedure_steps": [
        "діагностика стану нігтів і кутикули",
        "формування довжини",
        "обробка кутикули (класична / медична / японська)",
        "опційно: гель-лак / гель / френч / ombre",
        "догляд кремом"
    ],
    "contraindications": ["активне запалення нігтя / шкіри"],
    "aftercare_advice": "Олія для кутикули щодня. Регулярний візит раз на 3-4 тижні. Для гель-лаку — зняття у салоні."
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            # Real keys у gb.service
            kr = await session.execute(text(
                "SELECT DISTINCT canonical_key FROM gb.service WHERE archive=false AND canonical_key = ANY(:k)"
            ), {"k": MANICURE_KEYS})
            real_keys = sorted({row[0] for row in kr.all() if row[0]})
            if not real_keys:
                print("  ⚠ no real manicure keys")
                return

            # Зібрати key_descriptions з джерел
            all_descs: dict[str, str] = {}
            for name in DELETE_PROFILES + ["Інші процедури нігтьового сервісу"]:
                r = await session.execute(text(
                    "SELECT key_descriptions FROM service_profile WHERE country='gb' AND name=:n"
                ), {"n": name})
                row = r.first()
                if row:
                    for k, v in (row[0] or {}).items():
                        if k in real_keys and k not in all_descs and v:
                            all_descs[k] = v

            ex = await session.execute(text(
                "SELECT id FROM service_profile WHERE country='gb' AND name='Манікюр'"
            ))
            existing = ex.scalar()
            if existing:
                # Just update
                await session.execute(text(
                    "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb), key_descriptions=CAST(:d AS jsonb) WHERE id=:i"
                ), {"k": json.dumps(real_keys), "d": json.dumps(all_descs), "i": existing})
                print(f"  ↻ updated existing Манікюр GB ({len(real_keys)} keys)")
            else:
                primary = real_keys[0]
                chk = await session.execute(text(
                    "SELECT id FROM service_profile WHERE country='gb' AND canonical_key=:k"
                ), {"k": primary})
                if chk.scalar():
                    primary = f"manikyur_gb_{uuid.uuid4().hex[:8]}"
                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name="Манікюр",
                    country="gb", default_language="uk", enabled=True,
                    created_by="gb_manicure_merge_v1", updated_by="gb_manicure_merge_v1",
                    canonical_keys=real_keys, key_descriptions=all_descs,
                ))
                session.add(ServiceProfileTranslation(profile_id=pid, language="uk", **UK))
                await session.flush()
                print(f"  + created Манікюр GB ({len(real_keys)} keys)")

            # Delete sources
            d = await session.execute(text(
                "DELETE FROM service_profile WHERE country='gb' AND name = ANY(:n)"
            ), {"n": DELETE_PROFILES})
            print(f"  ✖ deleted {d.rowcount} source profiles")

            # Remove these keys from 'Інші процедури нігтьового сервісу'
            r = await session.execute(text(
                "SELECT id, canonical_keys FROM service_profile WHERE country='gb' AND name='Інші процедури нігтьового сервісу'"
            ))
            row = r.first()
            if row:
                cleaned = [k for k in (row[1] or []) if k not in real_keys]
                if len(cleaned) != len(row[1] or []):
                    await session.execute(text(
                        "UPDATE service_profile SET canonical_keys=CAST(:k AS jsonb) WHERE id=:id"
                    ), {"k": json.dumps(cleaned), "id": row[0]})
                    print(f"  − cleaned 'Інші процедури нігтьового сервісу' GB")

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
            print(f"  ↻ re-linked: {r.rowcount}")
            await session.commit()
            print("DONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
