"""Витягує canonical_keys per group для query-first profile creation.

Output: .logs/group_keys/<group_name>.json з реальними ключами + counts + brands + sample names.
Корисно для написання profile definitions БЕЗ guess'ів.

Запуск:
    python -m scripts.export_group_keys
    python -m scripts.export_group_keys --group "Нігті"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.db.models.category_group import CategoryGroup
from app.infrastructure.db.repositories.category_group_repo import CategoryGroupRepository
from app.infrastructure.db.session import build_engine, build_session_factory

OUTPUT_DIR = Path(__file__).parent.parent / ".logs" / "group_keys"


def slugify(s: str) -> str:
    from app.domain.services.canonical_key import _transliterate
    return re.sub(r"[^a-z0-9]+", "_", _transliterate(s.lower())).strip("_") or "group"


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", help="Filter by group name (substring)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            repo = CategoryGroupRepository(session)
            stmt = select(CategoryGroup).where(CategoryGroup.parent_group_id.is_(None))
            if args.group:
                stmt = stmt.where(CategoryGroup.name.ilike(f"%{args.group}%"))
            groups = list((await session.execute(stmt)).scalars())

            for g in groups:
                content = await repo.get_group_content(g.id)
                out = {
                    "group_name": g.name,
                    "group_id": g.id,
                    "subcategories": [
                        {
                            "name": s["name_normalized"],
                            "service_count": s["service_count"],
                            "category_ids": s["category_ids"],
                        }
                        for s in content["subcategories"]
                    ],
                    "parents": [
                        {
                            "name": p["name_normalized"],
                            "direct_service_count": p["direct_service_count"],
                            "category_ids": p["category_ids"],
                        }
                        for p in content["parents"]
                    ],
                    "canonical_keys": [
                        {
                            "canonical_key": ck["canonical_key"],
                            "brand": ck["brand"],
                            "service_count": ck["service_count"],
                            "sample_names": ck["sample_names"],
                            "countries": ck["countries"],
                            "has_profile": ck["has_profile"],
                        }
                        for ck in content["canonical_keys"]
                    ],
                }
                fp = OUTPUT_DIR / f"{slugify(g.name)}.json"
                fp.write_text(json.dumps(out, ensure_ascii=False, indent=2))
                print(f"  {g.name:30s} → {fp.name}  ({len(content['canonical_keys'])} keys)")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
