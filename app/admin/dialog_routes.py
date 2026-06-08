"""Admin → Діалоги. Список + перегляд історії з tool calls."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.repositories.dialog_repo import get_dialog, list_dialogs

dialog_router = APIRouter(prefix="/admin/dialogs", tags=["admin-dialogs"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@dialog_router.get("", response_class=HTMLResponse)
@dialog_router.get("/", response_class=HTMLResponse)
async def dialogs_list(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    country: str | None = None,
    salon_id: str | None = None,
    search: str | None = None,
    page: int = 1,
):
    data = await list_dialogs(
        session, country=country, salon_id=salon_id, search=search,
        page=page, page_size=50,
    )
    from datetime import datetime, timezone
    return templates.TemplateResponse(
        request,
        "dialogs/list.html",
        {
            "user": user, **data,
            "country": country, "salon_id": salon_id, "search": search,
            "now_utc": datetime.now(timezone.utc),
            "active_threshold_sec": 300,
        },
    )


@dialog_router.get("/{chat_id}", response_class=HTMLResponse)
async def dialog_detail(
    chat_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    dialog = await get_dialog(session, chat_id)
    if not dialog:
        raise HTTPException(404, "Dialog not found")
    return templates.TemplateResponse(
        request,
        "dialogs/detail.html",
        {"user": user, "dialog": dialog},
    )
