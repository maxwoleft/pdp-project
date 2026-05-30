"""Admin routes: керування категоріями + групами + створення профілів з canonical_keys.

URL-структура:
  /admin/category-tree                            — головний tree view
  /admin/category-tree/group/{group_id}           — відкрити групу (показ subcategories або canonical_keys)
  /admin/category-tree/groups                     — POST: створити group
  /admin/category-tree/groups/{id}                — PATCH: rename / DELETE
  /admin/category-tree/groups/{id}/add-members    — POST: bulk-add members
  /admin/category-tree/groups/{id}/remove-members — POST: bulk-remove
  /admin/category-tree/groups/{id}/create-profile — POST: створити ServiceProfile з вибраних canonical_keys
  /admin/category-tree/ai-suggest                 — POST: AI-suggest пропонує групування
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import (
    current_admin_user,
    get_category_group_repo,
)
from app.infrastructure.db.models.category_group import CategoryGroup
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileTranslation,
)
from app.infrastructure.db.repositories.category_group_repo import (
    CategoryGroupRepository,
)

router = APIRouter(prefix="/admin/category-tree", tags=["admin", "category-tree"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Tree view ─────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
async def tree_index(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
):
    """Top-level: всі groups + ungrouped parent-categories."""
    top_groups = await repo.list_top_groups()
    all_cats = await repo.get_all_categories_with_metadata()
    grouped_cats = [c for c in all_cats if c["fully_grouped"]]
    ungrouped_cats = [c for c in all_cats if not c["fully_grouped"]]

    return templates.TemplateResponse(
        request, "category_tree/index.html",
        {
            "user": user,
            "top_groups": top_groups,
            "ungrouped_categories": ungrouped_cats,
            "grouped_categories": grouped_cats,
        },
    )


@router.get("/group/{group_id}", response_class=HTMLResponse)
async def group_detail(
    group_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    subcat_filter: list[str] | None = Query(default=None),
    parent_filter: list[str] | None = Query(default=None),
):
    group = await repo.get_group(group_id)
    if not group:
        raise HTTPException(404, "group not found")

    child_groups = await repo.list_child_groups(group_id)

    subcategories: list = []
    parents: list = []
    canonical_keys: list = []
    direct_svc = 0
    active_subcat_filter: list[str] = subcat_filter or []
    active_parent_filter: list[str] = parent_filter or []

    if group.group_level == 1:
        content = await repo.get_group_content(
            group_id,
            active_subcat_filter or None,
            active_parent_filter or None,
        )
        canonical_keys = content["canonical_keys"]
        subcategories = content["subcategories"]
        parents = content["parents"]
        direct_svc = content["direct_service_count"]
    elif group.group_level == 2:
        # Subgroup (level 2): прямі subcategory members → keys
        sub_members = await repo.list_members(group_id, "subcategory")
        sub_cat_ids = [m.member_id for m in sub_members]
        canonical_keys = await repo.get_canonical_keys_for_categories(sub_cat_ids)

    # Список існуючих профілів для "add to existing"
    from sqlalchemy import select
    from app.infrastructure.db.models.profile import ServiceProfile
    profiles = list((await repo.session.execute(
        select(ServiceProfile).order_by(ServiceProfile.name)
    )).scalars())

    return templates.TemplateResponse(
        request, "category_tree/group.html",
        {
            "user": user,
            "group": group,
            "child_groups": child_groups,
            "subcategories": subcategories,
            "parents": parents,
            "canonical_keys": canonical_keys,
            "direct_service_count": direct_svc,
            "active_subcat_filter": active_subcat_filter,
            "active_parent_filter": active_parent_filter,
            "all_profiles": profiles,
        },
    )


# ── CRUD groups ───────────────────────────────────────────────────


@router.post("/groups")
async def create_group(
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    name: str = Form(...),
    parent_group_id: str | None = Form(None),
    notes: str | None = Form(None),
    member_type: str | None = Form(None),     # тип members що одразу додати
    member_ids: str | None = Form(None),       # JSON list або CSV
):
    if not name.strip():
        raise HTTPException(400, "name required")
    parent_id = parent_group_id or None
    try:
        group = await repo.create_group(name, parent_id, notes, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Опційно — одразу додати members
    if member_type and member_ids:
        ids = _parse_id_list(member_ids)
        if ids:
            await repo.add_members(group.id, member_type, ids)

    # Якщо HTMX — повертаємо partial; інакше redirect
    return RedirectResponse(f"/admin/category-tree/group/{group.id}", status_code=303)


@router.post("/groups/{group_id}/rename")
async def rename_group(
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    name: str = Form(...),
):
    ok = await repo.update_group(group_id, name=name, updated_by=user.email)
    if not ok:
        raise HTTPException(404, "group not found")
    return RedirectResponse(f"/admin/category-tree/group/{group_id}", status_code=303)


@router.post("/groups/{group_id}/delete")
async def delete_group(
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
):
    group = await repo.get_group(group_id)
    redirect_to = "/admin/category-tree"
    if group and group.parent_group_id:
        redirect_to = f"/admin/category-tree/group/{group.parent_group_id}"
    await repo.delete_group(group_id)
    return RedirectResponse(redirect_to, status_code=303)


# ── Members bulk-actions ──────────────────────────────────────────


@router.post("/groups/{group_id}/add-members")
async def add_members(
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    member_type: str = Form(...),
    member_ids: str = Form(...),    # JSON list або CSV
):
    ids = _parse_id_list(member_ids)
    if not ids:
        raise HTTPException(400, "member_ids required")
    if member_type not in ("parent_category", "subcategory", "canonical_key"):
        raise HTTPException(400, "invalid member_type")
    added = await repo.add_members(group_id, member_type, ids)
    return JSONResponse({"added": added})


@router.post("/groups/{group_id}/remove-members")
async def remove_members(
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    member_type: str = Form(...),
    member_ids: str = Form(...),
):
    ids = _parse_id_list(member_ids)
    if not ids:
        raise HTTPException(400, "member_ids required")
    removed = await repo.remove_members(group_id, member_type, ids)
    return JSONResponse({"removed": removed})


# ── Create profile from canonical_keys ────────────────────────────


@router.post("/groups/{group_id}/create-profile")
async def create_profile_from_keys(
    request: Request,
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    profile_name: str = Form(...),
    canonical_keys: str = Form(...),     # JSON list або CSV
):
    keys = _parse_id_list(canonical_keys)
    if not keys:
        raise HTTPException(400, "canonical_keys required")
    if not profile_name.strip():
        raise HTTPException(400, "profile_name required")

    session = repo.session  # use same session

    # Primary canonical_key = первший із keys (unique constraint)
    primary_key = keys[0]
    # Перевіримо чи нема колізії
    from sqlalchemy import select
    exists = (await session.execute(
        select(ServiceProfile).where(ServiceProfile.canonical_key == primary_key)
    )).scalar_one_or_none()
    if exists:
        primary_key = f"fam_{uuid.uuid4().hex[:12]}"

    profile_id = str(uuid.uuid4())
    profile = ServiceProfile(
        id=profile_id,
        canonical_key=primary_key,
        name=profile_name.strip(),
        country=None,
        default_language="uk",
        enabled=True,
        created_by=user.email,
        updated_by=user.email,
    )
    profile.canonical_keys = keys
    session.add(profile)

    # Empty UK translation (admin заповнить пізніше)
    session.add(ServiceProfileTranslation(
        profile_id=profile_id, language="uk",
        short_description=f"Профіль {profile_name.strip()}",
        addresses_problems=[], target_audience=[], benefits=[], keywords=[],
        sales_pitch=None, cross_sell=[], procedure_steps=[],
        contraindications=[], aftercare_advice=None,
    ))

    await session.flush()
    return RedirectResponse(f"/admin/profiles/{profile_id}", status_code=303)


@router.post("/groups/{group_id}/add-to-profile")
async def add_keys_to_existing_profile(
    group_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: CategoryGroupRepository = Depends(get_category_group_repo),
    profile_id: str = Form(...),
    canonical_keys: str = Form(...),
):
    """Додати вибрані canonical_keys до family-option існуючого profile."""
    keys = _parse_id_list(canonical_keys)
    if not keys:
        raise HTTPException(400, "canonical_keys required")

    from sqlalchemy import select
    session = repo.session

    profile = (await session.execute(
        select(ServiceProfile).where(ServiceProfile.id == profile_id)
    )).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "profile not found")

    # Profile-level: merge keys у profile.canonical_keys
    existing_keys = list(profile.canonical_keys or [])
    merged = sorted(set(existing_keys) | set(keys))
    profile.canonical_keys = merged
    await session.flush()
    return RedirectResponse(f"/admin/profiles/{profile_id}", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────


def _parse_id_list(raw: str) -> list[str]:
    """Парсить JSON-list або CSV — повертає список id."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    # CSV fallback
    return [s.strip() for s in raw.split(",") if s.strip()]
