"""Додає колонки до salon + employee у схемах ua/pl/gb.

Не використовуємо Alembic — лише raw ALTER TABLE IF NOT EXISTS. Ідемпотентний.

Salon: +code, +database_code, +data_dir, +status, +location_slug, +sort_order
Employee: +photo

Запуск: python -m scripts.migrate_salon_booking_fields
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine

COUNTRIES = ("ua", "pl", "gb")

SALON_ALTERS = [
    'ALTER TABLE "{c}".salon ADD COLUMN IF NOT EXISTS code VARCHAR(20)',
    'ALTER TABLE "{c}".salon ADD COLUMN IF NOT EXISTS database_code VARCHAR(20)',
    'ALTER TABLE "{c}".salon ADD COLUMN IF NOT EXISTS data_dir VARCHAR(255)',
    "ALTER TABLE \"{c}\".salon ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'planned'",
    'ALTER TABLE "{c}".salon ADD COLUMN IF NOT EXISTS location_slug VARCHAR(50)',
    'ALTER TABLE "{c}".salon ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0',
]

SALON_INDEXES = [
    'CREATE UNIQUE INDEX IF NOT EXISTS ix_{c}_salon_code ON "{c}".salon (code) WHERE code IS NOT NULL',
    'CREATE INDEX IF NOT EXISTS ix_{c}_salon_database_code ON "{c}".salon (database_code)',
    'CREATE INDEX IF NOT EXISTS ix_{c}_salon_location_slug ON "{c}".salon (location_slug)',
]

EMPLOYEE_ALTERS = [
    'ALTER TABLE "{c}".employee ADD COLUMN IF NOT EXISTS photo VARCHAR(500)',
]


async def main() -> None:
    engine = build_engine()
    async with engine.begin() as conn:
        for c in COUNTRIES:
            for stmt in SALON_ALTERS + EMPLOYEE_ALTERS + SALON_INDEXES:
                sql = stmt.format(c=c)
                await conn.execute(text(sql))
            print(f"[{c}] migrated salon + employee booking fields")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
