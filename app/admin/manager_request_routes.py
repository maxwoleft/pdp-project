"""Admin → Запит на менеджера: контакти від клієнтів які чекають консультації."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.infrastructure.db.models.eval import AdminUser

mgr_router = APIRouter(prefix="/admin/manager-requests", tags=["admin-mgr-req"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@mgr_router.get("", response_class=HTMLResponse)
@mgr_router.get("/", response_class=HTMLResponse)
async def list_requests(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
):
    where = "TRUE"
    params: dict = {}
    if status:
        where = "status = :status"
        params["status"] = status
    rows = (await session.execute(text(f"""
        SELECT id, country, salon_id, client_name, client_phone, client_question,
               status, created_at, resolved_at, notes
        FROM public.manager_request
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT 500
    """), params)).all()
    items = [
        {"id": str(r[0]), "country": r[1], "salon_id": r[2],
         "name": r[3], "phone": r[4], "question": r[5],
         "status": r[6], "created_at": r[7], "resolved_at": r[8], "notes": r[9]}
        for r in rows
    ]
    counts = (await session.execute(text("""
        SELECT status, COUNT(*) FROM public.manager_request GROUP BY status
    """))).all()
    status_counts = {r[0]: r[1] for r in counts}
    return templates.TemplateResponse(
        request,
        "manager_requests/list.html",
        {"user": user, "items": items, "status": status,
         "counts": status_counts},
    )


@mgr_router.post("/{req_id}/status")
async def update_status(
    req_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    new_status: str = Form(...),
    notes: str = Form(""),
):
    if new_status not in ("new", "contacted", "resolved"):
        raise HTTPException(400, "invalid status")
    await session.execute(text("""
        UPDATE public.manager_request
        SET status = :s, notes = :n,
            resolved_at = CASE WHEN :s = 'resolved' THEN now() ELSE resolved_at END
        WHERE id = :id
    """), {"s": new_status, "n": notes or None, "id": req_id})
    await session.commit()
    return RedirectResponse("/admin/manager-requests", status_code=303)
