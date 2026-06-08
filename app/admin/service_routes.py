"""Admin routes для перегляду каталогу послуг з фільтрами по країні/місту/салону."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.infrastructure.db.models.eval import AdminUser

service_router = APIRouter(prefix="/admin/services", tags=["admin-services"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PAGE_SIZE = 100


@service_router.get("", response_class=HTMLResponse)
@service_router.get("/", response_class=HTMLResponse)
async def services_list(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    country: str = "ua",
    city: str | None = None,
    salon_id: str | None = None,
    search: str | None = None,
    page: int = 1,
):
    page = max(1, page)

    # 1. Salons for filter dropdowns
    # Salon переїхав у booking.salons (global з country колонкою).
    salons_sql = sql_text(
        "SELECT id, name, city FROM booking.salons "
        "WHERE archive = false AND country = :country ORDER BY city, name"
    )
    salons_rows = (await session.execute(salons_sql, {"country": country})).fetchall()
    salons = [{"id": r[0], "name": r[1], "city": r[2]} for r in salons_rows]

    # Unique cities
    cities = sorted({s["city"] for s in salons if s["city"]})

    # Filter salons by city for dropdown
    if city:
        filtered_salons = [s for s in salons if s["city"] == city]
    else:
        filtered_salons = salons

    # 2. Build query
    where_parts = ["s.archive = false"]
    params: dict = {}

    if salon_id:
        where_parts.append("s.salon_id = :salon_id")
        params["salon_id"] = salon_id
    elif city:
        salon_ids_in_city = [s["id"] for s in salons if s["city"] == city]
        if salon_ids_in_city:
            placeholders = ", ".join(f":sid_{i}" for i in range(len(salon_ids_in_city)))
            where_parts.append(f"s.salon_id IN ({placeholders})")
            for i, sid in enumerate(salon_ids_in_city):
                params[f"sid_{i}"] = sid
        else:
            where_parts.append("1 = 0")

    if search:
        where_parts.append(
            "(s.name ILIKE :search OR s.name_uk ILIKE :search OR s.name_en ILIKE :search)"
        )
        params["search"] = f"%{search}%"

    where_clause = " AND ".join(where_parts)

    # Count
    count_sql = sql_text(f"SELECT COUNT(*) FROM {country}.service s WHERE {where_clause}")
    total = (await session.execute(count_sql, params)).scalar() or 0

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * PAGE_SIZE

    # Services
    services_sql = sql_text(f"""
        SELECT s.id, COALESCE(s.name_uk, s.name) as display_name,
               s.name as original_name,
               s.duration_min, s.description, s.price, s.price_currency,
               s.gender, s.canonical_key,
               sal.name as salon_name, sal.city as salon_city
        FROM {country}.service s
        JOIN booking.salons sal ON sal.id = s.salon_id
        WHERE {where_clause}
        ORDER BY sal.city, sal.name, s.name
        LIMIT :lim OFFSET :off
    """)
    params["lim"] = PAGE_SIZE
    params["off"] = offset

    rows = (await session.execute(services_sql, params)).fetchall()
    services = [
        {
            "id": r[0],
            "name": r[1],
            "original_name": r[2],
            "duration_min": r[3],
            "description": (r[4] or "")[:200],
            "price": float(r[5]) if r[5] else 0,
            "currency": r[6],
            "gender": r[7],
            "canonical_key": r[8],
            "salon_name": r[9],
            "salon_city": r[10],
        }
        for r in rows
    ]

    # Selected salon name for display
    selected_salon_name = None
    if salon_id:
        for s in salons:
            if s["id"] == salon_id:
                selected_salon_name = s["name"]
                break

    return templates.TemplateResponse(
        request,
        "services/list.html",
        {
            "user": user,
            "services": services,
            "country": country,
            "city": city,
            "salon_id": salon_id,
            "selected_salon_name": selected_salon_name,
            "search": search,
            "cities": cities,
            "salons": filtered_salons,
            "pagination": {
                "page": page,
                "page_size": PAGE_SIZE,
                "total": total,
                "total_pages": total_pages,
            },
        },
    )


@service_router.get("/api/salons", response_class=HTMLResponse)
async def api_salons_by_city(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    country: str = "ua",
    city: str | None = None,
):
    """HTMX endpoint: повертає <option> для select салонів при зміні міста."""
    sql = sql_text(
        "SELECT id, name FROM booking.salons WHERE archive = false AND country = :country "
        + ("AND city = :city " if city else "")
        + "ORDER BY name"
    )
    params = {"country": country}
    if city:
        params["city"] = city
    rows = (await session.execute(sql, params)).fetchall()

    options = '<option value="">Усі салони</option>'
    for r in rows:
        options += f'<option value="{r[0]}">{r[1]}</option>'
    return HTMLResponse(content=options)
