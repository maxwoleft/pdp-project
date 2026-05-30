"""Створює/оновлює всі салони мережі з каталогу.

Ідемпотентний (UPSERT по детермінованому UUID).
Запуск: python -m scripts.seed_salons
"""
import asyncio

from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.db.models.staff import Salon
from app.infrastructure.db.session import build_engine, build_session_factory, country_session
from app.integrations.crm.salons_catalog import SALONS, by_country


async def main() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)

    for country in ("ua", "pl", "gb"):
        salons = by_country(country)
        if not salons:
            continue
        async with country_session(factory, country) as session:
            for s in salons:
                stmt = insert(Salon).values(
                    id=s.salon_id,
                    name=s.name,
                    city=s.city,
                    address=s.address,
                    phone=s.phone,
                    timezone=s.timezone,
                    archive=False,
                ).on_conflict_do_update(
                    index_elements=[Salon.id],
                    set_={
                        "name": s.name,
                        "city": s.city,
                        "address": s.address,
                        "phone": s.phone,
                        "timezone": s.timezone,
                    },
                )
                await session.execute(stmt)
            print(f"[{country}] upserted {len(salons)} salons")

    await engine.dispose()
    print(f"Total salons in catalog: {len(SALONS)}")


if __name__ == "__main__":
    asyncio.run(main())
