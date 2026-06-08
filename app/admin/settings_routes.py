"""Admin → Налаштування: редагування system prompt instructions (markdown files)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import current_admin_user, get_session
from app.infrastructure.db.models.eval import AdminUser

log = logging.getLogger("settings")

settings_router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

INSTRUCTIONS_DIR = Path(__file__).parent.parent / "agents" / "instructions"


FILE_METADATA: dict[str, dict] = {
    "persona.md": {"title": "Persona", "desc": "Хто такий AI: ім'я, тон голосу, рольова модель", "section": "core"},
    "sales_dna.md": {"title": "Sales DNA", "desc": "Продажні принципи. Як ставитися до клієнта як продажник", "section": "core"},
    "golden_rules.md": {"title": "Золоті правила", "desc": "Найвищий пріоритет: anti-hallucination, tool calls обов'язкові, заборона вигадування", "section": "core"},
    "booking_flow.md": {"title": "Booking Flow", "desc": "Етапи створення запису. Як проводити клієнта до бронювання", "section": "flow"},
    "cancellation_flow.md": {"title": "Cancellation Flow", "desc": "Скасування / перенесення запису", "section": "flow"},
    "communication_style.md": {"title": "Стиль комунікації", "desc": "Як говорити: довжина повідомлень, форматування, пунктуація, формальність", "section": "style"},
    "tools_reference.md": {"title": "Tools Reference", "desc": "Коли який tool викликати. Поведінка при порожніх результатах", "section": "core"},
    "country_overrides/ua.md": {"title": "Override UA", "desc": "Специфіка для українських салонів (мова, ціни UAH, культурний контекст)", "section": "country"},
    "country_overrides/pl.md": {"title": "Override PL", "desc": "Польська специфіка (PLN, форми звертання, місцева культура)", "section": "country"},
    "country_overrides/gb.md": {"title": "Override GB", "desc": "Британська специфіка (GBP, англомовні клієнти у Лондоні)", "section": "country"},
}


def _list_editable_files() -> list[dict]:
    files = []
    for rel_path, meta in FILE_METADATA.items():
        full = INSTRUCTIONS_DIR / rel_path
        files.append({
            "path": rel_path,
            "title": meta["title"],
            "description": meta["desc"],
            "section": meta["section"],
            "exists": full.exists(),
            "size": full.stat().st_size if full.exists() else 0,
            "modified": full.stat().st_mtime if full.exists() else None,
        })
    # Diary examples — lower priority, auto-listed
    examples_dir = INSTRUCTIONS_DIR / "dialogue_examples"
    if examples_dir.exists():
        for f in sorted(examples_dir.glob("*.md")):
            files.append({
                "path": f"dialogue_examples/{f.name}",
                "title": f"Приклади: {f.stem.replace('_', ' ').title()}",
                "description": "Приклади діалогів для навчання AI як вести себе у типовій ситуації",
                "section": "examples",
                "exists": True,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return files


def _safe_path(rel: str) -> Path:
    """Захист від path traversal."""
    target = (INSTRUCTIONS_DIR / rel).resolve()
    base = INSTRUCTIONS_DIR.resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(400, "invalid path")
    if not target.suffix == ".md":
        raise HTTPException(400, "only .md files")
    return target


@settings_router.get("", response_class=HTMLResponse)
@settings_router.get("/", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
):
    files = _list_editable_files()
    sections = {
        "core": [f for f in files if f["section"] == "core"],
        "flow": [f for f in files if f["section"] == "flow"],
        "style": [f for f in files if f["section"] == "style"],
        "country": [f for f in files if f["section"] == "country"],
        "examples": [f for f in files if f["section"] == "examples"],
    }
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {"user": user, "sections": sections, "total": len(files)},
    )


@settings_router.get("/edit/{path:path}", response_class=HTMLResponse)
async def settings_edit(
    path: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    saved: int = 0,
    run_id: str | None = None,
):
    target = _safe_path(path)
    content = target.read_text(encoding="utf-8") if target.exists() else ""
    meta = FILE_METADATA.get(path, {})
    title = meta.get("title") or path
    desc = meta.get("desc") or ""
    return templates.TemplateResponse(
        request,
        "settings/edit.html",
        {
            "user": user, "path": path, "content": content,
            "title": title, "desc": desc, "saved": bool(saved),
            "exists": target.exists(),
            "test_run_id": run_id,
        },
    )


@settings_router.post("/edit/{path:path}")
async def settings_save(
    path: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
    content: str = Form(""),
):
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Backup before save (legacy .bak — keeps for compat)
    if target.exists():
        backup = target.with_suffix(".md.bak")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(content, encoding="utf-8")

    # DB version snapshot
    try:
        await session.execute(text("""
            INSERT INTO public.instruction_version (path, content, saved_by, size_bytes)
            VALUES (:p, :c, :u, :s)
        """), {"p": path, "c": content, "u": user.email, "s": len(content.encode("utf-8"))})
        await session.commit()
    except Exception as exc:
        log.warning("version snapshot failed for %s: %s", path, exc)

    # Invalidate agent cache so changes apply immediately
    af = getattr(request.app.state, "agent_factory", None)
    if af is not None:
        af.clear_cache()

    # Spawn background smoke test (~60s)
    run_id = None
    try:
        from app.admin.smoke_test import trigger_smoke_test
        run_id = await trigger_smoke_test(
            request.app.state.session_factory, path, user.email,
        )
    except Exception as exc:
        log.warning("smoke test trigger failed: %s", exc)

    suffix = f"&run_id={run_id}" if run_id else ""
    return RedirectResponse(f"/admin/settings/edit/{path}?saved=1{suffix}", status_code=303)


@settings_router.get("/test_run/{run_id}")
async def settings_test_status(
    run_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(text("""
        SELECT id, status, passed, total, details, started_at, finished_at, triggered_by_path
        FROM public.instruction_test_run WHERE id = :id
    """), {"id": run_id})).first()
    if not row:
        raise HTTPException(404)
    return {
        "id": str(row[0]), "status": row[1],
        "passed": row[2], "total": row[3],
        "details": row[4] or [],
        "started_at": row[5].isoformat() if row[5] else None,
        "finished_at": row[6].isoformat() if row[6] else None,
        "triggered_by_path": row[7],
    }


@settings_router.get("/history/{path:path}", response_class=HTMLResponse)
async def settings_history(
    path: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    _safe_path(path)  # validate
    rows = (await session.execute(text("""
        SELECT id, saved_by, saved_at, size_bytes,
               LEFT(content, 200) AS preview
        FROM public.instruction_version
        WHERE path = :p
        ORDER BY saved_at DESC
        LIMIT 100
    """), {"p": path})).all()
    versions = [
        {"id": str(r[0]), "saved_by": r[1], "saved_at": r[2],
         "size_bytes": r[3], "preview": r[4] or ""}
        for r in rows
    ]
    meta = FILE_METADATA.get(path, {})
    return templates.TemplateResponse(
        request,
        "settings/history.html",
        {"user": user, "path": path, "title": meta.get("title", path),
         "versions": versions},
    )


@settings_router.post("/restore/{version_id}")
async def settings_restore(
    version_id: str,
    request: Request,
    user: AdminUser = Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(text(
        "SELECT path, content FROM public.instruction_version WHERE id = :id"
    ), {"id": version_id})).first()
    if not row:
        raise HTTPException(404)
    path, content = row[0], row[1]
    target = _safe_path(path)
    # Snapshot current state before rollback
    if target.exists():
        current = target.read_text(encoding="utf-8")
        await session.execute(text("""
            INSERT INTO public.instruction_version (path, content, saved_by, size_bytes)
            VALUES (:p, :c, :u, :s)
        """), {"p": path, "c": current, "u": f"{user.email} (pre-restore)",
               "s": len(current.encode("utf-8"))})
    target.write_text(content, encoding="utf-8")
    await session.commit()
    # Invalidate cache
    af = getattr(request.app.state, "agent_factory", None)
    if af is not None:
        af.clear_cache()
    return RedirectResponse(f"/admin/settings/edit/{path}?saved=1", status_code=303)


@settings_router.post("/reload")
async def settings_reload(
    request: Request,
    user: AdminUser = Depends(current_admin_user),
):
    """Manual reload — clear agent cache."""
    af = getattr(request.app.state, "agent_factory", None)
    if af is not None:
        af.clear_cache()
    return RedirectResponse("/admin/settings?reloaded=1", status_code=303)
