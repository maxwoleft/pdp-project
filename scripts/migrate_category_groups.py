"""Schema migration: category_group + group_member tables.

category_group:
  - id UUID PK
  - name VARCHAR(255)            -- user-defined display name
  - parent_group_id UUID NULL    -- nested groups (max 3 levels)
  - group_level SMALLINT         -- 1 (top), 2 (subgroup), 3 (deepest)
  - sort_order INT
  - notes TEXT NULL              -- admin notes
  - created_by, updated_by, created_at, updated_at

group_member:
  - id UUID PK
  - group_id UUID FK
  - member_type VARCHAR(20)      -- 'parent_category' | 'subcategory' | 'canonical_key'
  - member_id VARCHAR(255)       -- category.id (per-salon) OR canonical_key
  - sort_order INT
  - UNIQUE (group_id, member_type, member_id)

Прив'язка по category.id (per-salon) — стійко до перейменування в CRM.
Idempotent. Запуск:
    python -m scripts.migrate_category_groups
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine

DDL = """
CREATE TABLE IF NOT EXISTS public.category_group (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    parent_group_id UUID NULL REFERENCES public.category_group(id) ON DELETE CASCADE,
    group_level SMALLINT NOT NULL DEFAULT 1 CHECK (group_level BETWEEN 1 AND 3),
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT NULL,
    created_by VARCHAR(255) NULL,
    updated_by VARCHAR(255) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_category_group_parent
    ON public.category_group (parent_group_id);
CREATE INDEX IF NOT EXISTS ix_category_group_sort
    ON public.category_group (sort_order);

CREATE TABLE IF NOT EXISTS public.group_member (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES public.category_group(id) ON DELETE CASCADE,
    member_type VARCHAR(20) NOT NULL CHECK (member_type IN ('parent_category', 'subcategory', 'canonical_key')),
    member_id VARCHAR(255) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, member_type, member_id)
);

CREATE INDEX IF NOT EXISTS ix_group_member_group
    ON public.group_member (group_id);
CREATE INDEX IF NOT EXISTS ix_group_member_lookup
    ON public.group_member (member_type, member_id);
"""


async def main() -> None:
    engine = build_engine()
    try:
        async with engine.begin() as conn:
            for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
                await conn.execute(text(stmt))
        print("DONE: category_group + group_member created")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
