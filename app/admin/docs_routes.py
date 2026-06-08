"""Admin → Документація. Tabs: Опис технології, Керування."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import current_admin_user
from app.infrastructure.db.models.eval import AdminUser

docs_router = APIRouter(prefix="/admin/docs", tags=["admin-docs"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@docs_router.get("", response_class=HTMLResponse)
@docs_router.get("/", response_class=HTMLResponse)
async def docs_index(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    tab: str = "tech",
):
    return templates.TemplateResponse(
        request,
        "docs/index.html",
        {"user": user, "active_tab": tab},
    )
