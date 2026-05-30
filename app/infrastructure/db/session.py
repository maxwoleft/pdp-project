"""Async engine + per-country session.

Кожна країна = окрема Postgres схема (ua, pl, gb).
Перемикання — через SET search_path на рівні сесії.
Спільні довідники (country) живуть у public.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def country_session(
    session_factory: async_sessionmaker[AsyncSession], country: str
) -> AsyncIterator[AsyncSession]:
    """Відкриває сесію з search_path = <country>, public.

    Гарантує, що всі запити йдуть у схему конкретної країни.
    """
    country = country.lower()
    async with session_factory() as session:
        await session.execute(text(f'SET search_path TO "{country}", public'))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
