"""ALTER TABLE для додавання структурованих атрибутів у service.

Без alembic: проект на metadata.create_all, тому міграція руками.
Це idempotent — IF NOT EXISTS захищає від повторного запуску.

Запуск:
    python -m scripts.migrate_service_attributes
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine

COUNTRIES = ("ua", "pl", "gb")

DDL = """
ALTER TABLE {schema}.service ADD COLUMN IF NOT EXISTS brand VARCHAR(100);
ALTER TABLE {schema}.service ADD COLUMN IF NOT EXISTS volume_ml NUMERIC(8, 2);
ALTER TABLE {schema}.service ADD COLUMN IF NOT EXISTS zones SMALLINT;
ALTER TABLE {schema}.service ADD COLUMN IF NOT EXISTS session_minutes SMALLINT;
ALTER TABLE {schema}.service ADD COLUMN IF NOT EXISTS ampules SMALLINT;
CREATE INDEX IF NOT EXISTS ix_{schema}_service_brand
    ON {schema}.service (brand) WHERE brand IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_{schema}_service_canonical_brand
    ON {schema}.service (canonical_key, brand);
"""


async def amain() -> None:
    engine = build_engine()
    try:
        async with engine.begin() as conn:
            for country in COUNTRIES:
                stmts = [s.strip() for s in DDL.format(schema=country).split(";") if s.strip()]
                for stmt in stmts:
                    await conn.execute(text(stmt))
                print(f"[{country}] migrated")
    finally:
        await engine.dispose()
    print("DONE")


if __name__ == "__main__":
    asyncio.run(amain())
