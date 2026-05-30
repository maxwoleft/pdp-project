"""Заповнює content для auto-created 'Нарощування волосся' profile UA + клонує на PL/GB."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


CONTENT = {
    "short_description": "Нарощування волосся капсульним, тейповим, мікро-кільцевим методом. Додає довжину і обʼєм. Різні системи — 50г, 100г, 150г.",
    "addresses_problems": [
        "тонке і рідке волосся — хочеться обʼєму",
        "коротке волосся — потрібна довжина за один візит",
        "хочеться змінити образ перед подією",
        "регулярна підтримка нарощеного волосся",
    ],
    "target_audience": [
        "клієнти з тонким або коротким волоссям",
        "перед весіллям / фотосесією",
        "регулярний догляд за нарощеним волоссям",
    ],
    "benefits": [
        "доступні методи: капсульне, тейпове, мікро-кільцеве, нано-капсульне",
        "вага систем: 50г, 100г, 150г — під ваші задачі",
        "тримається 3-6 місяців з підтримкою",
        "природний результат при правильному підборі",
    ],
    "keywords": ["нарощування волосся", "капсульне", "тейпове", "мікрокапсульне", "наростити волосся", "hair extensions"],
    "sales_pitch": "Раджу спершу консультацію — підберемо метод і вагу під ваше волосся. Капсульне для природного результату, тейпове для швидкого зняття. Підтримка раз на 2-3 місяці.",
    "cross_sell": [
        "укладка нарощеного волосся регулярно",
        "догляд преміум-брендами для нарощеного",
        "корекція системи раз на 2-3 місяці",
    ],
    "procedure_steps": [
        "консультація з підбором методу і відтінку",
        "миття волосся",
        "розділення на зони і кріплення капсул/стрічок",
        "стрижка під нарощене",
        "укладка",
    ],
    "contraindications": [
        "активне випадання волосся (треба спершу лікування)",
        "себорея і дерматити шкіри голови",
    ],
    "aftercare_advice": "Гребінець спеціальний для нарощеного. Без сильно гарячої сушки на коренях. Шампуні без сульфатів. Корекція раз на 2-3 місяці.",
}


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            for country in ("ua", "pl", "gb"):
                # Знайти existing auto_extend або відсутній profile
                r = await session.execute(text("""
                    SELECT id FROM public.service_profile
                    WHERE country=:c AND (name='Нарощування волосся' OR created_by='auto_extend')
                """), {"c": country})
                existing_ids = [row[0] for row in r.all()]

                # Знайти real keys у service з naroshchuvann + volos pattern
                kr = await session.execute(text(f"""
                    SELECT DISTINCT canonical_key FROM {country}.service
                    WHERE archive=false AND canonical_key IS NOT NULL
                    AND (canonical_key LIKE '%naroshchuvann%volos%' OR canonical_key LIKE '%volos%naroshchuvann%'
                         OR canonical_key LIKE '%kapsulne_naroshchuvann%' OR canonical_key LIKE '%tape_naroshchuvann%'
                         OR canonical_key LIKE '%kapsulamy_mikro_naroshchuvann%' OR canonical_key LIKE '%nano_naroshchuvann%')
                """))
                real_keys = sorted({row[0] for row in kr.all() if row[0]})
                if not real_keys:
                    print(f"[{country}] no real keys for naroshchuvannya — skip")
                    continue
                primary = real_keys[0]

                # Delete existing і recreate чистим
                if existing_ids:
                    await session.execute(text(
                        "DELETE FROM public.service_profile WHERE id = ANY(:ids)"
                    ), {"ids": existing_ids})
                    await session.flush()
                    print(f"[{country}] deleted {len(existing_ids)} existing 'Нарощування волосся'")

                # Перевірити чи primary не зайнятий
                chk = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                ), {"c": country, "k": primary})
                if chk.scalar():
                    print(f"[{country}] primary {primary} зайнятий — використовую placeholder")
                    primary = f"naroshch_volos_{country}_{uuid.uuid4().hex[:8]}"

                pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=pid, canonical_key=primary, name="Нарощування волосся",
                    country=country, default_language="uk", enabled=True,
                    created_by="naroshch_v1", updated_by="naroshch_v1",
                ))
                session.add(ServiceProfileTranslation(
                    profile_id=pid, language="uk", **CONTENT,
                ))
                session.add(ServiceProfileOption(
                    profile_id=pid, option_type="family", name="Нарощування волосся",
                    sort_order=0, canonical_keys=real_keys, **CONTENT,
                ))
                print(f"[{country}] created Нарощування волосся with {len(real_keys)} keys")

            await session.commit()
            print("\nDONE.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
