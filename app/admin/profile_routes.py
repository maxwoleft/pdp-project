"""Admin routes для service profiles, translations, variants, versions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.domain.services.canonical_key import normalize_to_canonical_key
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository

profile_router = APIRouter(prefix="/admin/profiles", tags=["admin-profiles"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _translation_to_dict(t) -> dict:
    if t is None:
        return {}
    return {
        "short_description": t.short_description or "",
        "detailed_description": t.detailed_description or "",
        "addresses_problems": list(t.addresses_problems or []),
        "target_audience": list(t.target_audience or []),
        "benefits": list(t.benefits or []),
        "keywords": list(t.keywords or []),
        "procedure_steps": list(t.procedure_steps or []),
        "contraindications": list(t.contraindications or []),
        "cross_sell": list(t.cross_sell or []),
        "aftercare_advice": t.aftercare_advice or "",
        "duration_typical_min": t.duration_typical_min or "",
        "sales_pitch": t.sales_pitch or "",
    }


# Регіструємо як Jinja глобал
templates.env.globals["translation_to_dict"] = _translation_to_dict

# Один embedder на роутер — переюзаємо connection pool
_embedder: OpenAIEmbedder | None = None


def get_embedder() -> OpenAIEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = OpenAIEmbedder()
    return _embedder


def get_profile_repo(
    session: AsyncSession = Depends(get_session),
) -> ServiceProfileRepository:
    return ServiceProfileRepository(session, embedder=get_embedder())


SUPPORTED_LANGUAGES = ["uk", "ru", "en", "pl"]
LANGUAGE_NAMES = {
    "uk": "Українська",
    "ru": "Русский",
    "en": "English",
    "pl": "Polski",
}


# ── List ──────────────────────────────────────────────────────────

@profile_router.get("", response_class=HTMLResponse)
@profile_router.get("/", response_class=HTMLResponse)
async def profiles_list(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    search: str | None = None,
    country: str = "ua",
    salon_id: str | None = None,
):
    profiles = await repo.list_all(country=country, salon_id=salon_id, search=search)
    stats = await repo.stats()
    coverage = await repo.coverage_stats()

    from sqlalchemy import text as sql_text

    # Список салонів обраної країни (для фільтра)
    salons_rows = (await repo.session.execute(sql_text(
        f"SELECT id, name, city FROM {country}.salon WHERE archive=false ORDER BY city, name"
    ))).all()
    salons = [{"id": r[0], "name": r[1], "city": r[2]} for r in salons_rows]

    # Per-profile counts: canonical_keys + services у країні (з salon-фільтром)
    rows_data = []
    unique_keys: set[str] = set()
    for p in profiles:
        keys = list(p.canonical_keys or [])
        svc_count = 0
        if keys and p.country in ("ua", "pl", "gb"):
            if salon_id:
                r = await repo.session.execute(sql_text(
                    f"SELECT COUNT(*) FROM {p.country}.service "
                    f"WHERE archive=false AND salon_id=:s AND canonical_key = ANY(:k)"
                ), {"s": salon_id, "k": keys})
            else:
                r = await repo.session.execute(sql_text(
                    f"SELECT COUNT(*) FROM {p.country}.service "
                    f"WHERE archive=false AND canonical_key = ANY(:k)"
                ), {"k": keys})
            svc_count = r.scalar() or 0
        rows_data.append({
            "profile": p,
            "keys_count": len(keys),
            "services_count": svc_count,
        })
        unique_keys.update(keys)

    # Total unique keys + services покрито в country (через service.profile_id)
    total_keys = len(unique_keys)
    if salon_id:
        r = await repo.session.execute(sql_text(
            f"SELECT COUNT(*) FROM {country}.service "
            f"WHERE archive=false AND salon_id=:s AND profile_id IS NOT NULL"
        ), {"s": salon_id})
        total_services_covered = r.scalar() or 0
        r = await repo.session.execute(sql_text(
            f"SELECT COUNT(*) FROM {country}.service WHERE archive=false AND salon_id=:s"
        ), {"s": salon_id})
        total_services_all = r.scalar() or 0
    else:
        r = await repo.session.execute(sql_text(
            f"SELECT COUNT(*) FROM {country}.service "
            f"WHERE archive=false AND profile_id IS NOT NULL"
        ))
        total_services_covered = r.scalar() or 0
        r = await repo.session.execute(sql_text(
            f"SELECT COUNT(*) FROM {country}.service WHERE archive=false"
        ))
        total_services_all = r.scalar() or 0

    return templates.TemplateResponse(
        request,
        "profiles/list.html",
        {
            "user": user,
            "rows_data": rows_data,
            "profiles": profiles,  # legacy slot
            "stats": stats,
            "coverage": coverage,
            "search": search,
            "country": country,
            "salon_id": salon_id,
            "salons": salons,
            "total_keys": total_keys,
            "total_services_covered": total_services_covered,
            "total_services_all": total_services_all,
            "languages": SUPPORTED_LANGUAGES,
            "language_names": LANGUAGE_NAMES,
        },
    )


# ── Missing profiles ──────────────────────────────────────────────

@profile_router.get("/missing", response_class=HTMLResponse)
async def profiles_missing(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    country: str = "ua",
    search: str | None = None,
    page: int = 1,
    page_size: int = 200,
):
    pagination = await repo.list_missing_canonical_keys(
        country=country, search=search, page=page, page_size=page_size
    )
    coverage = await repo.coverage_stats()
    profiles_for_dropdown = await repo.list_all(country=country, enabled_only=False)
    profile_list = [{"id": p.id, "name": p.name} for p in profiles_for_dropdown]

    # Останні авто-присвоєння (read latest log files)
    import json as _json
    from pathlib import Path as _Path
    auto_link_entries: list[dict] = []
    log_dir = _Path(".logs/auto_link")
    if log_dir.exists():
        files = sorted(log_dir.glob(f"{country}_*.json"), reverse=True)[:5]
        for f in files:
            try:
                d = _json.loads(f.read_text())
                for e in d.get("entries", []):
                    if e.get("action") == "linked":
                        auto_link_entries.append({
                            **e,
                            "timestamp": d.get("timestamp", ""),
                        })
            except Exception:
                continue
    auto_link_entries = auto_link_entries[:50]  # ліміт показу

    return templates.TemplateResponse(
        request,
        "profiles/missing.html",
        {
            "user": user,
            "items": pagination["items"],
            "pagination": pagination,
            "coverage": coverage,
            "country": country,
            "search": search,
            "profile_list": profile_list,
            "auto_link_entries": auto_link_entries,
            "languages": SUPPORTED_LANGUAGES,
            "language_names": LANGUAGE_NAMES,
        },
    )


@profile_router.post("/{profile_id}/add-canonical-key")
async def add_canonical_key_to_profile(
    profile_id: str,
    canonical_key: str = Form(...),
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    """Додати canonical_key до profile.canonical_keys + re-link services."""
    from sqlalchemy import text as sql_text
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404, "profile not found")
    keys = list(profile.canonical_keys or [])
    if canonical_key not in keys:
        keys.append(canonical_key)
        profile.canonical_keys = keys
        await repo.session.flush()
    # Re-link services у відповідній country
    if profile.country in ("ua", "pl", "gb"):
        await repo.session.execute(sql_text(
            f"UPDATE {profile.country}.service SET profile_id = :pid "
            f"WHERE archive=false AND canonical_key = :k AND profile_id IS NULL"
        ), {"pid": str(profile.id), "k": canonical_key})
    return {"ok": True, "profile_id": profile_id, "canonical_key": canonical_key}


# ── New profile ───────────────────────────────────────────────────

@profile_router.get("/new", response_class=HTMLResponse)
async def profile_new_form(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    canonical_key: str | None = None,
    name: str | None = None,
):
    """Підтримує prefill через query params: ?canonical_key=...&name=...
    Для quick-create з missing list."""
    return templates.TemplateResponse(
        request,
        "profiles/new.html",
        {
            "user": user,
            "error": None,
            "languages": SUPPORTED_LANGUAGES,
            "language_names": LANGUAGE_NAMES,
            "form": {
                "canonical_key": canonical_key or "",
                "name": name or "",
            },
        },
    )


@profile_router.post("/new", response_class=HTMLResponse)
async def profile_new_submit(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    name: str = Form(...),
    canonical_key: str = Form(...),
    country: str = Form(""),
    default_language: str = Form("uk"),
    short_description: str = Form(...),
    addresses_problems: str = Form("[]"),
    benefits: str = Form("[]"),
    keywords: str = Form("[]"),
    sales_pitch: str = Form(""),
):
    err = None
    if not name or len(name) < 3:
        err = "Name мінімум 3 символи"
    elif not canonical_key or not re.match(r"^[a-z0-9_]+$", canonical_key):
        err = "Canonical key — lowercase, цифри і underscore"
    elif country.strip() not in ("ua", "pl", "gb"):
        err = "Country обовʼязковий: ua / pl / gb"
    elif default_language not in SUPPORTED_LANGUAGES:
        err = f"Default language має бути один з: {', '.join(SUPPORTED_LANGUAGES)}"
    elif not short_description or len(short_description) < 10:
        err = "Short description мінімум 10 символів"

    if err:
        return templates.TemplateResponse(
            request,
            "profiles/new.html",
            {
                "user": user,
                "error": err,
                "languages": SUPPORTED_LANGUAGES,
                "language_names": LANGUAGE_NAMES,
                "form": {
                    "name": name,
                    "canonical_key": canonical_key,
                    "country": country,
                    "default_language": default_language,
                    "short_description": short_description,
                },
            },
            status_code=400,
        )

    existing = await repo.get_by_canonical_key(canonical_key, country=country.strip())
    if existing:
        return templates.TemplateResponse(
            request,
            "profiles/new.html",
            {
                "user": user,
                "error": f"Профіль з canonical_key '{canonical_key}' вже існує",
                "languages": SUPPORTED_LANGUAGES,
                "language_names": LANGUAGE_NAMES,
                "form": {"name": name, "canonical_key": canonical_key},
            },
            status_code=400,
        )

    profile = await repo.create(
        name=name.strip(),
        canonical_key=canonical_key.strip(),
        country=country.strip(),
        default_language=default_language,
        created_by=user.email,
        updated_by=user.email,
    )

    # Створюємо першу translation за дефолтну мову
    await repo.upsert_translation(
        profile.id,
        default_language,
        short_description=short_description.strip(),
        addresses_problems=_parse_json_list(addresses_problems),
        benefits=_parse_json_list(benefits),
        keywords=_parse_json_list(keywords),
        sales_pitch=sales_pitch.strip() or None,
    )

    # Initial version
    await repo.save_version(
        profile.id,
        change_summary="Initial creation",
        created_by=user.email,
    )

    return RedirectResponse(f"/admin/profiles/{profile.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────

@profile_router.get("/{profile_id}", response_class=HTMLResponse)
async def profile_detail(
    profile_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)

    translations = await repo.list_translations(profile_id)
    variants = await repo.list_variants(profile_id)
    versions = await repo.list_versions(profile_id)

    # Counts of linked services per country
    linked_counts = {
        "ua": await repo.count_services_for_canonical_key(profile.canonical_key, "ua"),
        "pl": await repo.count_services_for_canonical_key(profile.canonical_key, "pl"),
        "gb": await repo.count_services_for_canonical_key(profile.canonical_key, "gb"),
    }

    # Усі canonical_keys через options + per-key services per country
    from sqlalchemy import text as sql_text
    opts: list = []  # legacy slot; options layer removed

    # Profile-level keys + descriptions (винесене з options)
    profile_keys = list(profile.canonical_keys or [])
    profile_descs = dict(profile.key_descriptions or {})
    all_keys = set(profile_keys)
    if profile.canonical_key:
        all_keys.add(profile.canonical_key)

    # services per key (only profile country) — skip keys без послуг
    keys_breakdown: list[dict] = []
    for k in sorted(all_keys):
        rows = (await repo.session.execute(sql_text(f"""
            SELECT s.id, s.name, s.brand, s.price, s.price_currency, s.duration_min, s.salon_id
            FROM {profile.country}.service s
            WHERE s.archive=false AND s.canonical_key = :k
            ORDER BY s.name
        """), {"k": k})).all()
        if not rows:
            continue
        services = [{
            "id": r.id, "name": r.name, "brand": r.brand,
            "price": float(r.price) if r.price else None,
            "currency": r.price_currency, "duration_min": r.duration_min,
            "salon_id": r.salon_id,
        } for r in rows]
        # Clean sample name: відрізати EN/RU/UA-prefixes якщо є
        sample_name = services[0]["name"]
        if "UA " in sample_name:
            parts = [p.strip() for p in sample_name.split("/")]
            for part in parts:
                if part.startswith("UA "):
                    sample_name = part[3:].strip()
                    break
        keys_breakdown.append({
            "canonical_key": k,
            "services": services,
            "description": profile_descs.get(k, ""),
            "sample_name": sample_name,
        })

    return templates.TemplateResponse(
        request,
        "profiles/detail.html",
        {
            "user": user,
            "profile": profile,
            "translations": translations,
            "variants": variants,
            "versions": versions[:10],
            "linked_counts": linked_counts,
            "options": opts,
            "keys_breakdown": keys_breakdown,
            "languages": SUPPORTED_LANGUAGES,
            "language_names": LANGUAGE_NAMES,
        },
    )


# ── Profile-level key descriptions (AJAX) ─────────────────────────

@profile_router.post("/{profile_id}/key-description")
async def save_key_description_profile(
    profile_id: str,
    canonical_key: str = Form(...),
    description: str = Form(""),
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    """Inline AJAX — оновлює profile.key_descriptions[canonical_key]."""
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    kd = dict(profile.key_descriptions or {})
    desc = description.strip()
    if desc:
        kd[canonical_key] = desc
    else:
        kd.pop(canonical_key, None)
    profile.key_descriptions = kd
    await repo.session.flush()
    return {"ok": True, "canonical_key": canonical_key, "description": desc}


# ── Edit translation (main editor) ────────────────────────────────

@profile_router.get("/{profile_id}/edit/{language}", response_class=HTMLResponse)
async def profile_edit_translation(
    profile_id: str,
    language: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, "Unsupported language")

    translation = await repo.get_translation(profile_id, language)

    return templates.TemplateResponse(
        request,
        "profiles/edit_translation.html",
        {
            "user": user,
            "profile": profile,
            "language": language,
            "language_names": LANGUAGE_NAMES,
            "translation": translation,
            "error": None,
        },
    )


@profile_router.post("/{profile_id}/edit/{language}", response_class=HTMLResponse)
async def profile_edit_translation_submit(
    profile_id: str,
    language: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    short_description: str = Form(...),
    detailed_description: str = Form(""),
    addresses_problems: str = Form("[]"),
    target_audience: str = Form("[]"),
    benefits: str = Form("[]"),
    keywords: str = Form("[]"),
    procedure_steps: str = Form("[]"),
    contraindications: str = Form("[]"),
    aftercare_advice: str = Form(""),
    cross_sell: str = Form("[]"),
    duration_typical_min: str = Form(""),
    sales_pitch: str = Form(""),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)

    duration = None
    if duration_typical_min.strip():
        try:
            duration = int(duration_typical_min)
        except ValueError:
            pass

    await repo.upsert_translation(
        profile_id,
        language,
        short_description=short_description.strip(),
        detailed_description=detailed_description.strip() or None,
        addresses_problems=_parse_json_list(addresses_problems),
        target_audience=_parse_json_list(target_audience),
        benefits=_parse_json_list(benefits),
        keywords=_parse_json_list(keywords),
        procedure_steps=_parse_json_list(procedure_steps),
        contraindications=_parse_json_list(contraindications),
        aftercare_advice=aftercare_advice.strip() or None,
        cross_sell=_parse_json_list(cross_sell),
        duration_typical_min=duration,
        sales_pitch=sales_pitch.strip() or None,
    )

    # Update profile updated_at + save version
    await repo.update_fields(profile_id, updated_by=user.email)
    await repo.save_version(
        profile_id,
        change_summary=f"Updated {LANGUAGE_NAMES.get(language, language)} translation",
        created_by=user.email,
    )

    return RedirectResponse(f"/admin/profiles/{profile_id}", status_code=303)


# ── Delete profile ────────────────────────────────────────────────

@profile_router.delete("/{profile_id}")
async def profile_delete(
    profile_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    deleted = await repo.delete(profile_id)
    if not deleted:
        raise HTTPException(404)
    return Response(status_code=200)


# ── Versions / Rollback ───────────────────────────────────────────

@profile_router.get("/{profile_id}/versions", response_class=HTMLResponse)
async def profile_versions(
    profile_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    versions = await repo.list_versions(profile_id)
    return templates.TemplateResponse(
        request,
        "profiles/versions.html",
        {"user": user, "profile": profile, "versions": versions},
    )


@profile_router.post("/{profile_id}/versions/{version_number}/rollback")
async def profile_rollback(
    profile_id: str,
    version_number: int,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    ok = await repo.rollback_to_version(profile_id, version_number, actor=user.email)
    if not ok:
        raise HTTPException(404)
    return RedirectResponse(f"/admin/profiles/{profile_id}", status_code=303)


# ── A/B Variants ──────────────────────────────────────────────────

@profile_router.get("/{profile_id}/variants", response_class=HTMLResponse)
async def profile_variants(
    profile_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    variants = await repo.list_variants(profile_id)
    return templates.TemplateResponse(
        request,
        "profiles/variants.html",
        {
            "user": user,
            "profile": profile,
            "variants": variants,
            "languages": SUPPORTED_LANGUAGES,
            "language_names": LANGUAGE_NAMES,
        },
    )


@profile_router.post("/{profile_id}/variants/new")
async def profile_variant_create(
    profile_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    label: str = Form(...),
    language: str = Form(...),
    short_description: str = Form(...),
    sales_pitch: str = Form(""),
    addresses_problems: str = Form("[]"),
    benefits: str = Form("[]"),
    keywords: str = Form("[]"),
    weight: str = Form("50"),
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    try:
        weight_int = int(weight)
    except ValueError:
        weight_int = 50
    await repo.create_variant(
        profile_id=profile_id,
        language=language,
        label=label.strip(),
        short_description=short_description.strip(),
        sales_pitch=sales_pitch.strip() or None,
        addresses_problems=_parse_json_list(addresses_problems),
        benefits=_parse_json_list(benefits),
        keywords=_parse_json_list(keywords),
        weight=weight_int,
    )
    return RedirectResponse(f"/admin/profiles/{profile_id}/variants", status_code=303)


@profile_router.delete("/{profile_id}/variants/{variant_id}")
async def profile_variant_delete(
    profile_id: str,
    variant_id: str,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
):
    await repo.delete_variant(variant_id)
    return Response(status_code=200)


# ── Linked services ───────────────────────────────────────────────

@profile_router.get("/{profile_id}/services", response_class=HTMLResponse)
async def profile_linked_services(
    profile_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    country: str = "ua",
):
    profile = await repo.get(profile_id)
    if not profile:
        raise HTTPException(404)
    services = await repo.services_for_profile(profile_id, country)
    return templates.TemplateResponse(
        request,
        "profiles/services.html",
        {
            "user": user,
            "profile": profile,
            "services": services,
            "country": country,
        },
    )


# ── Utils ─────────────────────────────────────────────────────────

@profile_router.get("/api/preview-canonical-key")
async def api_preview_canonical_key(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    repo: ServiceProfileRepository = Depends(get_profile_repo),
    name: str = "",
    canonical_key: str = "",
):
    """API: повертає згенерований canonical_key + кількість послуг + sample list."""
    from sqlalchemy import text as sql_text

    key = canonical_key.strip() if canonical_key.strip() else normalize_to_canonical_key(name)
    if not key:
        return {"key": "", "total": 0, "by_country": {}, "services": []}

    counts: dict[str, int] = {}
    total = 0
    services: list[dict] = []

    session = repo.session
    for c in ("ua", "pl", "gb"):
        n = await repo.count_services_for_canonical_key(key, c)
        counts[c] = n
        total += n

        # Sample services (до 10 на країну)
        sql = sql_text(
            f"""
            SELECT COALESCE(name_uk, name) as display_name,
                   salon_id, duration_min, price, price_currency, gender
            FROM {c}.service
            WHERE canonical_key = :key AND archive = false
            ORDER BY price
            LIMIT 10
            """
        )
        rows = await session.execute(sql, {"key": key})
        for row in rows.fetchall():
            services.append({
                "name": row[0],
                "country": c.upper(),
                "salon_id": str(row[1])[:8],
                "duration": row[2],
                "price": float(row[3]) if row[3] else 0,
                "currency": row[4],
                "gender": row[5],
            })

    return {"key": key, "total": total, "by_country": counts, "services": services}


def _parse_json_list(raw: str) -> list:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []
