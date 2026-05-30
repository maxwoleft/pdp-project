"""Створює схеми ua/pl/gb та накатує всі таблиці в кожну.

Запуск: python -m scripts.init_db

Для MVP використовуємо metadata.create_all замість Alembic.
Коли схема стабілізується — переведемо на Alembic міграції.
"""
import asyncio

from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import *  # noqa: F401,F403  — реєструємо моделі
from app.infrastructure.db.session import build_engine

COUNTRIES = [
    ("ua", "Україна", "Europe/Kyiv", "UAH", "uk"),
    ("pl", "Polska", "Europe/Warsaw", "PLN", "pl"),
    ("gb", "United Kingdom", "Europe/London", "GBP", "en"),
]


async def main() -> None:
    settings = get_settings()  # noqa: F841
    engine = build_engine()

    async with engine.begin() as conn:
        # 0. pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # 1. public schema + країни + country_messenger
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
        from app.infrastructure.db.models.common import Country, CountryMessenger  # noqa
        await conn.run_sync(lambda c: Country.__table__.create(c, checkfirst=True))
        await conn.run_sync(lambda c: CountryMessenger.__table__.create(c, checkfirst=True))

        # Сід країн
        for code, name, tz, currency, lang in COUNTRIES:
            await conn.execute(
                text(
                    "INSERT INTO public.country(code, name, timezone, currency, default_lang) "
                    "VALUES (:code, :name, :tz, :cur, :lang) ON CONFLICT (code) DO NOTHING"
                ),
                {"code": code, "name": name, "tz": tz, "cur": currency, "lang": lang},
            )

        # 2. Створюємо схему на кожну країну і всі country-specific таблиці
        country_tables = [
            t for t in Base.metadata.sorted_tables if t.schema is None
        ]
        for code, *_ in COUNTRIES:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{code}"'))
            await conn.execute(text(f'SET search_path TO "{code}", public'))
            for table in country_tables:
                await conn.run_sync(lambda c, t=table: t.create(c, checkfirst=True))

    await engine.dispose()
    print("DB initialized: schemas =", [c[0] for c in COUNTRIES])


if __name__ == "__main__":
    asyncio.run(main())
