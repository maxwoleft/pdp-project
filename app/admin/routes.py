"""Admin panel routes — login, dashboard, scenarios CRUD."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.admin.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_session_token,
    verify_password,
)
from app.admin.deps import (
    current_admin_user,
    current_admin_user_optional,
    get_scenario_repo,
    get_user_repo,
)
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.repositories.eval_repo import (
    AdminUserRepository,
    EvalScenarioRepository,
)

router = APIRouter(prefix="/admin", tags=["admin"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SLUG_RE = re.compile(r"^[a-z0-9_]+$")


# ── Login / Logout ────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: AdminUser | None = Depends(current_admin_user_optional),
):
    if user:
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "email": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    user_repo: AdminUserRepository = Depends(get_user_repo),
):
    user = await user_repo.get_by_email(email)
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Невірний email або пароль", "email": email},
            status_code=401,
        )

    await user_repo.touch_login(user.id)
    token = create_session_token(user.id, user.email)

    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # ⚠️ True для продакшену з HTTPS
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ── Dashboard ─────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
    days: int = 30,
):
    from app.admin.deps import get_session as _get_session
    from app.admin.dashboard_metrics import compute_metrics
    stats = await repo.stats()
    # Use session manually (Depends fragmented)
    sf = request.app.state.session_factory
    async with sf() as s:
        metrics = await compute_metrics(s, days=days)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "stats": stats, "metrics": metrics, "days": days},
    )


# ── Scenarios List ────────────────────────────────────────────────

@router.get("/scenarios", response_class=HTMLResponse)
async def scenarios_list(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
    search: str | None = None,
    country: str | None = None,
    tag: str | None = None,
):
    scenarios = await repo.list_all(country=country, tag=tag, search=search)
    all_tags = await repo.all_tags()
    return templates.TemplateResponse(
        request,
        "scenarios/list.html",
        {
            "user": user,
            "scenarios": scenarios,
            "all_tags": all_tags,
            "search": search,
            "country": country,
            "tag": tag,
        },
    )


# ── New Scenario ──────────────────────────────────────────────────

@router.get("/scenarios/new", response_class=HTMLResponse)
async def scenario_new_form(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
):
    form_data = {
        "slug": "",
        "country": "ua",
        "description": "",
        "preset_salon_id": "",
        "enabled": True,
        "turns": [],
        "reference_responses": [],
        "expectations": [],
        "must_not": [],
        "tags": [],
    }
    return templates.TemplateResponse(
        request,
        "scenarios/form.html",
        {"user": user, "scenario": None, "form_data": form_data, "error": None},
    )


@router.post("/scenarios/new", response_class=HTMLResponse)
async def scenario_new_submit(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
    slug: str = Form(...),
    country: str = Form(...),
    description: str = Form(...),
    preset_salon_id: str = Form(""),
    enabled: str | None = Form(None),
    turns: str = Form("[]"),
    reference_responses: str = Form("[]"),
    expectations: str = Form("[]"),
    must_not: str = Form("[]"),
    tags: str = Form("[]"),
):
    err = _validate_form(slug, country, description, turns, expectations)
    if err:
        return templates.TemplateResponse(
            request,
            "scenarios/form.html",
            {
                "user": user,
                "scenario": None,
                "form_data": _form_data_from_request(
                    slug, country, description, preset_salon_id, enabled,
                    turns, reference_responses, expectations, must_not, tags,
                ),
                "error": err,
            },
            status_code=400,
        )

    existing = await repo.get_by_slug(slug)
    if existing:
        return templates.TemplateResponse(
            request,
            "scenarios/form.html",
            {
                "user": user,
                "scenario": None,
                "form_data": _form_data_from_request(
                    slug, country, description, preset_salon_id, enabled,
                    turns, reference_responses, expectations, must_not, tags,
                ),
                "error": f"Сценарій з slug '{slug}' вже існує",
            },
            status_code=400,
        )

    await repo.create(
        slug=slug.strip(),
        country=country.strip().lower(),
        description=description.strip(),
        preset_salon_id=(preset_salon_id or None) and preset_salon_id.strip() or None,
        enabled=enabled is not None,
        turns=_parse_json_list(turns),
        reference_responses=_parse_json_list(reference_responses),
        expectations=_parse_json_list(expectations),
        must_not=_parse_json_list(must_not),
        tags=_parse_json_list(tags),
        created_by=user.email,
        updated_by=user.email,
    )
    return RedirectResponse("/admin/scenarios", status_code=303)


# ── Detail ────────────────────────────────────────────────────────

@router.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
async def scenario_detail(
    scenario_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
):
    scenario = await repo.get_by_id(scenario_id)
    if not scenario:
        raise HTTPException(404, "scenario not found")
    return templates.TemplateResponse(
        request,
        "scenarios/detail.html",
        {"user": user, "scenario": scenario},
    )


# ── Edit ──────────────────────────────────────────────────────────

@router.get("/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
async def scenario_edit_form(
    scenario_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
):
    scenario = await repo.get_by_id(scenario_id)
    if not scenario:
        raise HTTPException(404, "scenario not found")
    form_data = {
        "slug": scenario.slug,
        "country": scenario.country,
        "description": scenario.description,
        "preset_salon_id": scenario.preset_salon_id or "",
        "enabled": scenario.enabled,
        "turns": scenario.turns or [],
        "reference_responses": scenario.reference_responses or [],
        "expectations": scenario.expectations or [],
        "must_not": scenario.must_not or [],
        "tags": scenario.tags or [],
    }
    return templates.TemplateResponse(
        request,
        "scenarios/form.html",
        {"user": user, "scenario": scenario, "form_data": form_data, "error": None},
    )


@router.post("/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
async def scenario_edit_submit(
    scenario_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
    slug: str = Form(...),
    country: str = Form(...),
    description: str = Form(...),
    preset_salon_id: str = Form(""),
    enabled: str | None = Form(None),
    turns: str = Form("[]"),
    reference_responses: str = Form("[]"),
    expectations: str = Form("[]"),
    must_not: str = Form("[]"),
    tags: str = Form("[]"),
):
    scenario = await repo.get_by_id(scenario_id)
    if not scenario:
        raise HTTPException(404, "scenario not found")

    err = _validate_form(slug, country, description, turns, expectations)
    if err:
        return templates.TemplateResponse(
            request,
            "scenarios/form.html",
            {
                "user": user,
                "scenario": scenario,
                "form_data": _form_data_from_request(
                    slug, country, description, preset_salon_id, enabled,
                    turns, reference_responses, expectations, must_not, tags,
                ),
                "error": err,
            },
            status_code=400,
        )

    # Перевірка унікальності slug якщо змінився
    if slug != scenario.slug:
        existing = await repo.get_by_slug(slug)
        if existing:
            return templates.TemplateResponse(
                request,
                "scenarios/form.html",
                {
                    "user": user,
                    "scenario": scenario,
                    "form_data": _form_data_from_request(
                        slug, country, description, preset_salon_id, enabled,
                        turns, reference_responses, expectations, must_not, tags,
                    ),
                    "error": f"Сценарій з slug '{slug}' вже існує",
                },
                status_code=400,
            )

    await repo.update(
        scenario_id,
        slug=slug.strip(),
        country=country.strip().lower(),
        description=description.strip(),
        preset_salon_id=(preset_salon_id or None) and preset_salon_id.strip() or None,
        enabled=enabled is not None,
        turns=_parse_json_list(turns),
        reference_responses=_parse_json_list(reference_responses),
        expectations=_parse_json_list(expectations),
        must_not=_parse_json_list(must_not),
        tags=_parse_json_list(tags),
        updated_by=user.email,
    )
    return RedirectResponse(f"/admin/scenarios/{scenario_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────

@router.delete("/scenarios/{scenario_id}")
async def scenario_delete(
    scenario_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: EvalScenarioRepository = Depends(get_scenario_repo),
):
    deleted = await repo.delete(scenario_id)
    if not deleted:
        raise HTTPException(404, "scenario not found")
    # HTMX очікує порожню відповідь з 200 щоб видалити row
    return Response(status_code=200, content="")


# ── Helpers ───────────────────────────────────────────────────────

def _parse_json_list(raw: str) -> list:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []


def _validate_form(slug, country, description, turns_raw, expectations_raw) -> str | None:
    if not slug or not SLUG_RE.match(slug):
        return "Slug має бути lowercase, цифри і underscore (наприклад: search_basic)"
    if country not in ("ua", "pl", "gb"):
        return "Country має бути ua, pl або gb"
    if not description or len(description) < 5:
        return "Опис обов'язковий (мінімум 5 символів)"
    turns = _parse_json_list(turns_raw)
    if not turns:
        return "Має бути хоча б один turn"
    expectations = _parse_json_list(expectations_raw)
    if not expectations:
        return "Має бути хоча б одна expectation"
    return None


def _form_data_from_request(
    slug, country, description, preset_salon_id, enabled,
    turns, reference_responses, expectations, must_not, tags,
) -> dict:
    return {
        "slug": slug,
        "country": country,
        "description": description,
        "preset_salon_id": preset_salon_id,
        "enabled": enabled is not None,
        "turns": _parse_json_list(turns),
        "reference_responses": _parse_json_list(reference_responses),
        "expectations": _parse_json_list(expectations),
        "must_not": _parse_json_list(must_not),
        "tags": _parse_json_list(tags),
    }
