"""Admin → Помилки бота. Перегляд + позначка resolved."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.infrastructure.db.models.eval import AdminUser

error_router = APIRouter(prefix="/admin/errors", tags=["admin-errors"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@error_router.get("", response_class=HTMLResponse)
@error_router.get("/", response_class=HTMLResponse)
async def list_errors(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    resolved: str | None = None,
):
    where = "TRUE"
    if resolved == "yes":
        where = "resolved = true"
    elif resolved == "no":
        where = "resolved = false"
    rows = (await session.execute(text(f"""
        SELECT id, chat_id, country, salon_id, source, error_type,
               error_message, traceback, resolved, created_at
        FROM public.bot_error
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT 300
    """))).all()
    items = [
        {"id": str(r[0]), "chat_id": r[1], "country": r[2], "salon_id": r[3],
         "source": r[4], "error_type": r[5], "error_message": r[6],
         "traceback": r[7], "resolved": r[8], "created_at": r[9]}
        for r in rows
    ]
    counts = (await session.execute(text("""
        SELECT resolved, COUNT(*) FROM public.bot_error GROUP BY resolved
    """))).all()
    cmap = {r[0]: r[1] for r in counts}
    return templates.TemplateResponse(
        request,
        "errors/list.html",
        {"user": user, "items": items, "resolved": resolved,
         "unresolved_count": cmap.get(False, 0),
         "resolved_count": cmap.get(True, 0)},
    )


@error_router.post("/{err_id}/resolve")
async def resolve(
    err_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(text(
        "UPDATE public.bot_error SET resolved = NOT resolved WHERE id = :id"
    ), {"id": err_id})
    await session.commit()
    return RedirectResponse("/admin/errors", status_code=303)


@error_router.post("/{err_id}/delete")
async def delete(
    err_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(text("DELETE FROM public.bot_error WHERE id = :id"), {"id": err_id})
    await session.commit()
    return RedirectResponse("/admin/errors", status_code=303)
