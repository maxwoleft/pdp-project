"""Створює/оновлює всі салони мережі з каталогу.

Ідемпотентний (UPSERT по детермінованому UUID).
Salon живе у booking.salons (глобально) — країна вказується колонкою country.
Запуск: python -m scripts.seed_salons
"""
import asyncio

from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.db.models.staff import Salon
from app.infrastructure.db.session import build_engine, build_session_factory
from app.integrations.crm.salons_catalog import SALONS


async def main() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)

    async with factory() as session:
        for s in SALONS:
            values = dict(
                id=s.salon_id,
                code=s.code,
                country=s.country,
                name=s.name,
                city=s.city,
                location_slug=s.location_slug,
                sort_order=s.sort_order,
                status=s.status,
                timezone=s.timezone,
                archive=False,
                database_code=s.database_code,
                data_dir=s.data_dir,
                address_line=s.address,
                phone_display=s.phone,
            )
            stmt = insert(Salon).values(**values).on_conflict_do_update(
                index_elements=[Salon.id],
                set_={k: v for k, v in values.items() if k != "id"},
            )
            await session.execute(stmt)
        await session.commit()
        print(f"Upserted {len(SALONS)} salons into booking.salons")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
