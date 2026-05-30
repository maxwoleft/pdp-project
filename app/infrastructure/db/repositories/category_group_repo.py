"""Repository: category groups + tree query for admin UI.

Tree-структура для admin:
  level 0: top groups (parent_group_id IS NULL)
    level 1: child groups
      level 2: deepest groups
  + ungrouped items (parent_categories not in any group)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.category_group import CategoryGroup, GroupMember

COUNTRIES = ("ua", "pl", "gb")


class CategoryGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── CRUD groups ──────────────────────────────────────────────────

    async def create_group(
        self,
        name: str,
        parent_group_id: str | None = None,
        notes: str | None = None,
        created_by: str | None = None,
    ) -> CategoryGroup:
        # Auto-detect group_level з parent
        level = 1
        if parent_group_id:
            parent = await self.get_group(parent_group_id)
            if parent:
                if parent.group_level >= 3:
                    raise ValueError("Max nesting depth 3 reached")
                level = parent.group_level + 1
        group = CategoryGroup(
            name=name.strip(),
            parent_group_id=parent_group_id,
            group_level=level,
            notes=notes,
            created_by=created_by,
            updated_by=created_by,
        )
        self.session.add(group)
        await self.session.flush()
        return group

    async def get_group(self, group_id: str) -> CategoryGroup | None:
        return (await self.session.execute(
            select(CategoryGroup).where(CategoryGroup.id == group_id)
        )).scalar_one_or_none()

    async def update_group(
        self, group_id: str, name: str | None = None, notes: str | None = None,
        sort_order: int | None = None, updated_by: str | None = None,
    ) -> bool:
        values: dict = {"updated_by": updated_by}
        if name is not None:
            values["name"] = name.strip()
        if notes is not None:
            values["notes"] = notes
        if sort_order is not None:
            values["sort_order"] = sort_order
        result = await self.session.execute(
            update(CategoryGroup).where(CategoryGroup.id == group_id).values(**values)
        )
        return result.rowcount > 0

    async def delete_group(self, group_id: str) -> bool:
        result = await self.session.execute(
            delete(CategoryGroup).where(CategoryGroup.id == group_id)
        )
        return result.rowcount > 0

    async def list_top_groups(self) -> list[CategoryGroup]:
        return list((await self.session.execute(
            select(CategoryGroup).where(CategoryGroup.parent_group_id.is_(None))
            .order_by(CategoryGroup.sort_order, CategoryGroup.name)
        )).scalars())

    async def list_child_groups(self, parent_id: str) -> list[CategoryGroup]:
        return list((await self.session.execute(
            select(CategoryGroup).where(CategoryGroup.parent_group_id == parent_id)
            .order_by(CategoryGroup.sort_order, CategoryGroup.name)
        )).scalars())

    # ── Members ──────────────────────────────────────────────────────

    async def add_members(
        self, group_id: str, member_type: str, member_ids: list[str]
    ) -> int:
        """Bulk add members. Ігнорує дублі (UNIQUE constraint)."""
        added = 0
        for mid in member_ids:
            try:
                self.session.add(GroupMember(
                    group_id=group_id, member_type=member_type, member_id=mid
                ))
                await self.session.flush()
                added += 1
            except Exception:
                await self.session.rollback()
                # Перезапускаємо session для наступних
                # (для async-safe цей паттерн потребує savepoint)
                continue
        return added

    async def remove_members(
        self, group_id: str, member_type: str, member_ids: list[str]
    ) -> int:
        result = await self.session.execute(
            delete(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.member_type == member_type,
                GroupMember.member_id.in_(member_ids),
            )
        )
        return result.rowcount or 0

    async def list_members(
        self, group_id: str, member_type: str | None = None
    ) -> list[GroupMember]:
        stmt = select(GroupMember).where(GroupMember.group_id == group_id)
        if member_type:
            stmt = stmt.where(GroupMember.member_type == member_type)
        stmt = stmt.order_by(GroupMember.sort_order, GroupMember.member_id)
        return list((await self.session.execute(stmt)).scalars())

    # ── Tree query (для admin UI) ────────────────────────────────────

    async def get_all_categories_with_metadata(self) -> list[dict]:
        """Усі parent_categories з усіх країн з підрахунком sub-cats і services.

        Повертає список:
        [{
            "name_normalized": str,       # нормалізована UA-назва
            "category_ids": [str],        # всі id (per-salon) з цією назвою
            "salon_ids": [str],           # в яких салонах присутня
            "countries": [str],
            "subcategory_count": int,
            "service_count": int,
            "in_group_id": str | None,    # ID групи якщо вже в групі
        }, ...]
        """
        from app.domain.services.canonical_key import (
            extract_uk_part_from_crm
        )

        rows: list[dict] = []
        for c in COUNTRIES:
            result = await self.session.execute(text(f"""
                SELECT cat.id AS cat_id, cat.name AS cat_name, cat.salon_id,
                       (SELECT COUNT(*) FROM {c}.category sc WHERE sc.parent_id = cat.id AND sc.archive=false) AS sub_count,
                       (SELECT COUNT(*) FROM {c}.service s WHERE s.category_id = cat.id AND s.archive=false) AS svc_count_direct,
                       (SELECT COUNT(*) FROM {c}.service s JOIN {c}.category sc ON sc.id = s.category_id
                        WHERE sc.parent_id = cat.id AND s.archive=false) AS svc_count_indirect
                FROM {c}.category cat
                WHERE cat.archive = false AND cat.parent_id IS NULL
            """))
            for r in result.all():
                rows.append({
                    "category_id": r.cat_id,
                    "name_raw": r.cat_name,
                    "name_normalized": extract_uk_part_from_crm(r.cat_name or "").strip(),
                    "salon_id": r.salon_id,
                    "country": c,
                    "subcategory_count": r.sub_count or 0,
                    "service_count": (r.svc_count_direct or 0) + (r.svc_count_indirect or 0),
                })

        # Aggregate by normalized name
        aggregated: dict[str, dict] = {}
        for r in rows:
            key = r["name_normalized"] or r["name_raw"]
            if key not in aggregated:
                aggregated[key] = {
                    "name_normalized": key,
                    "category_ids": [],
                    "salon_ids": [],
                    "countries": set(),
                    "subcategory_count": 0,
                    "service_count": 0,
                }
            agg = aggregated[key]
            agg["category_ids"].append(r["category_id"])
            agg["salon_ids"].append(r["salon_id"])
            agg["countries"].add(r["country"])
            agg["subcategory_count"] += r["subcategory_count"]
            agg["service_count"] += r["service_count"]

        # Які category_ids уже в группі
        in_group = await self.session.execute(text("""
            SELECT member_id, group_id FROM public.group_member
            WHERE member_type = 'parent_category'
        """))
        in_group_map = {row.member_id: row.group_id for row in in_group.all()}

        result_list = []
        for k, agg in aggregated.items():
            in_group_ids = [in_group_map[cid] for cid in agg["category_ids"] if cid in in_group_map]
            agg["in_group_id"] = in_group_ids[0] if in_group_ids else None
            agg["fully_grouped"] = all(cid in in_group_map for cid in agg["category_ids"])
            agg["countries"] = sorted(list(agg["countries"]))
            result_list.append(agg)

        result_list.sort(key=lambda x: (-x["service_count"], x["name_normalized"]))
        return result_list

    async def get_subcategories_for_group(self, group_id: str) -> list[dict]:
        """Підкатегорії що входять до parent-категорій, які в цій групі."""
        # Отримуємо category_ids parent-катів цієї групи
        parent_cat_ids = [m.member_id for m in await self.list_members(group_id, "parent_category")]
        if not parent_cat_ids:
            return []

        from app.domain.services.canonical_key import extract_uk_part_from_crm

        rows: list[dict] = []
        # Розбиваємо category.id по country (id = "salon_id:crm_id")
        for c in COUNTRIES:
            result = await self.session.execute(text(f"""
                SELECT sub.id AS sub_id, sub.name AS sub_name, sub.parent_id, sub.salon_id,
                       (SELECT COUNT(*) FROM {c}.service s WHERE s.category_id = sub.id AND s.archive=false) AS svc_count
                FROM {c}.category sub
                WHERE sub.archive = false AND sub.parent_id = ANY(:pids)
            """), {"pids": parent_cat_ids})
            for r in result.all():
                rows.append({
                    "category_id": r.sub_id,
                    "name_raw": r.sub_name,
                    "name_normalized": extract_uk_part_from_crm(r.sub_name or "").strip(),
                    "salon_id": r.salon_id,
                    "country": c,
                    "service_count": r.svc_count or 0,
                })

        # Aggregate
        agg: dict[str, dict] = {}
        for r in rows:
            key = r["name_normalized"] or r["name_raw"]
            if key not in agg:
                agg[key] = {
                    "name_normalized": key,
                    "category_ids": [],
                    "service_count": 0,
                    "countries": set(),
                }
            agg[key]["category_ids"].append(r["category_id"])
            agg[key]["service_count"] += r["service_count"]
            agg[key]["countries"].add(r["country"])

        # Які з sub-categories уже в підгрупах
        in_subgroup = await self.session.execute(text("""
            SELECT member_id, group_id FROM public.group_member
            WHERE member_type = 'subcategory'
        """))
        in_subgroup_map = {row.member_id: row.group_id for row in in_subgroup.all()}

        result_list = []
        for k, item in agg.items():
            in_g = [in_subgroup_map[cid] for cid in item["category_ids"] if cid in in_subgroup_map]
            item["in_group_id"] = in_g[0] if in_g else None
            item["countries"] = sorted(list(item["countries"]))
            result_list.append(item)

        result_list.sort(key=lambda x: (-x["service_count"], x["name_normalized"]))
        return result_list

    async def get_group_content(
        self,
        group_id: str,
        subcategory_filter: list[str] | None = None,
        parent_filter: list[str] | None = None,
    ) -> dict:
        """Повертає для level-1 групи:
          - canonical_keys: всі ключі (services直під parents + через всі subcats),
            відфільтровані по subcategory_filter АБО parent_filter.
            Якщо нічого не вибрано → все.
          - subcategories: підкатегорії з лічильниками — для UI-фільтра.
          - parents: parent-категорії групи з лічильниками — для UI-фільтра.
          - direct_service_count: послуги прямо в parents (без subcategory).
        """
        from app.domain.services.canonical_key import extract_uk_part_from_crm

        parent_cat_ids = [
            m.member_id for m in await self.list_members(group_id, "parent_category")
        ]
        if not parent_cat_ids:
            return {
                "canonical_keys": [], "subcategories": [], "parents": [],
                "direct_service_count": 0,
            }

        # 1. Знайти всі subcategories (по name aggreгуємо).
        # svc_count рахуємо рекурсивно — direct + усі descendants — щоб не показувати
        # порожні placeholder-subcategories CRM.
        sub_rows: list[dict] = []
        for c in COUNTRIES:
            result = await self.session.execute(text(f"""
                WITH RECURSIVE sub_tree AS (
                    SELECT sub.id AS root_id, sub.id AS cat_id, sub.name AS root_name,
                           sub.salon_id AS root_salon
                    FROM {c}.category sub
                    WHERE sub.archive = false AND sub.parent_id = ANY(:pids)
                    UNION ALL
                    SELECT st.root_id, c.id, st.root_name, st.root_salon
                    FROM {c}.category c
                    JOIN sub_tree st ON c.parent_id = st.cat_id
                    WHERE c.archive = false
                )
                SELECT st.root_id AS id, st.root_name AS name, st.root_salon AS salon_id,
                       COUNT(s.id) AS svc_count
                FROM sub_tree st
                LEFT JOIN {c}.service s ON s.category_id = st.cat_id AND s.archive = false
                GROUP BY st.root_id, st.root_name, st.root_salon
            """), {"pids": parent_cat_ids})
            for r in result.all():
                sub_rows.append({
                    "id": r.id,
                    "name_normalized": extract_uk_part_from_crm(r.name or "").strip() or r.name,
                    "salon_id": r.salon_id,
                    "country": c,
                    "service_count": r.svc_count or 0,
                })

        # Aggregate subcategories by name
        sub_agg: dict[str, dict] = {}
        for r in sub_rows:
            key = r["name_normalized"]
            if key not in sub_agg:
                sub_agg[key] = {
                    "name_normalized": key,
                    "category_ids": [],
                    "service_count": 0,
                    "countries": set(),
                }
            sub_agg[key]["category_ids"].append(r["id"])
            sub_agg[key]["service_count"] += r["service_count"]
            sub_agg[key]["countries"].add(r["country"])

        subcategories = []
        for k, v in sub_agg.items():
            # Пропускаємо порожні placeholder-subcategories CRM
            if v["service_count"] <= 0:
                continue
            v["countries"] = sorted(list(v["countries"]))
            subcategories.append(v)
        subcategories.sort(key=lambda x: (-x["service_count"], x["name_normalized"]))

        # 2. Збираємо parent-categories з direct svc count для UI filter
        parents_agg: dict[str, dict] = {}
        for c in COUNTRIES:
            result = await self.session.execute(text(f"""
                SELECT p.id, p.name, p.salon_id,
                       (SELECT COUNT(*) FROM {c}.service s
                        WHERE s.category_id = p.id AND s.archive=false) AS direct_svc
                FROM {c}.category p
                WHERE p.id = ANY(:pids) AND p.archive = false
            """), {"pids": parent_cat_ids})
            for r in result.all():
                key = extract_uk_part_from_crm(r.name or "").strip() or r.name
                if key not in parents_agg:
                    parents_agg[key] = {
                        "name_normalized": key, "category_ids": [],
                        "direct_service_count": 0, "countries": set(),
                    }
                parents_agg[key]["category_ids"].append(r.id)
                parents_agg[key]["direct_service_count"] += r.direct_svc or 0
                parents_agg[key]["countries"].add(c)
        parents_list = []
        for k, v in parents_agg.items():
            v["countries"] = sorted(list(v["countries"]))
            parents_list.append(v)
        # Сортуємо: спочатку ті у кого є direct services
        parents_list.sort(key=lambda x: (-x["direct_service_count"], x["name_normalized"]))

        # 3. Визначаємо category_ids для пошуку послуг
        all_sub_ids = [r["id"] for r in sub_rows]
        sub_filter_set = set(subcategory_filter or [])
        parent_filter_set = set(parent_filter or [])

        if not sub_filter_set and not parent_filter_set:
            # Все
            search_cat_ids = parent_cat_ids + all_sub_ids
        else:
            search_cat_ids: list[str] = []
            # Додаємо ВСІ керовані parent-cat ids (direct services + descendants підуть через recursive в get_canonical_keys_for_categories)
            for name, agg in parents_agg.items():
                if name in parent_filter_set:
                    search_cat_ids.extend(agg["category_ids"])
            # Додаємо ids вибраних subcategories
            for name, agg in sub_agg.items():
                if name in sub_filter_set:
                    search_cat_ids.extend(agg["category_ids"])

        # 4. Підрахунок direct services
        direct_svc = sum(p["direct_service_count"] for p in parents_list)

        canonical_keys = await self.get_canonical_keys_for_categories(search_cat_ids)

        return {
            "canonical_keys": canonical_keys,
            "subcategories": subcategories,
            "parents": parents_list,
            "direct_service_count": direct_svc,
        }

    async def get_canonical_keys_for_categories(self, category_ids: list[str]) -> list[dict]:
        """canonical_keys в межах вказаних категорій + descendants (recursive) + counts per country."""
        if not category_ids:
            return []

        from app.domain.services.canonical_key import extract_uk_part_from_crm

        # Рекурсивно розгортаємо category_ids → всі descendants (CRM може мати 3+ рівні).
        rows: list[dict] = []
        for c in COUNTRIES:
            result = await self.session.execute(text(f"""
                WITH RECURSIVE cat_tree AS (
                    SELECT id FROM {c}.category
                    WHERE archive = false AND id = ANY(:cids)
                    UNION
                    SELECT c.id FROM {c}.category c
                    JOIN cat_tree t ON c.parent_id = t.id
                    WHERE c.archive = false
                )
                SELECT s.canonical_key,
                       s.brand,
                       array_agg(DISTINCT s.name ORDER BY s.name) AS sample_names,
                       COUNT(*) AS svc_count
                FROM {c}.service s
                WHERE s.archive = false AND s.canonical_key IS NOT NULL
                  AND s.category_id IN (SELECT id FROM cat_tree)
                GROUP BY s.canonical_key, s.brand
            """), {"cids": category_ids})
            for r in result.all():
                rows.append({
                    "canonical_key": r.canonical_key,
                    "brand": r.brand,
                    "country": c,
                    "sample_names": (r.sample_names or [])[:3],
                    "service_count": r.svc_count or 0,
                })

        # Aggregate per canonical_key+brand
        agg: dict[tuple, dict] = {}
        for r in rows:
            key = (r["canonical_key"], r["brand"])
            if key not in agg:
                agg[key] = {
                    "canonical_key": r["canonical_key"],
                    "brand": r["brand"],
                    "sample_names": set(),
                    "service_count": 0,
                    "countries": set(),
                }
            agg[key]["sample_names"].update(r["sample_names"])
            agg[key]["service_count"] += r["service_count"]
            agg[key]["countries"].add(r["country"])

        # Які canonical_keys уже в profile
        in_profile = await self.session.execute(text("""
            SELECT DISTINCT jsonb_array_elements_text(canonical_keys::jsonb) AS k
            FROM public.service_profile WHERE canonical_keys IS NOT NULL
        """))
        in_profile_keys = {row.k for row in in_profile.all() if row.k}

        result_list = []
        for k, item in agg.items():
            item["sample_names"] = sorted(list(item["sample_names"]))[:3]
            item["countries"] = sorted(list(item["countries"]))
            item["has_profile"] = item["canonical_key"] in in_profile_keys
            result_list.append(item)

        result_list.sort(key=lambda x: (-x["service_count"], x["canonical_key"]))
        return result_list
