"""Web chat endpoints.

GET  /chat                  — onboarding (вибір країни/салону) або редірект на /chat/c
GET  /chat/c                — головна сторінка чату
POST /api/chat/country      — встановити країну (онбординг)
POST /api/chat/send         — текст
POST /api/chat/voice        — аудіо (multipart) → Whisper → AI
POST /api/chat/image        — фото (multipart) → Vision → AI
POST /api/chat/reset        — скинути session (новий діалог)
GET  /api/chat/salons       — список салонів країни (для онбордингу/picker)
GET  /api/chat/history      — історія діалогу (для рендеру при rejoin)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text as sql_text

from app.adapters.transcription.openai_whisper import OpenAIWhisper
from app.api.chat.salon_registry import (
    SALON_REGISTRY,
    lookup_by_slug,
    lookup_by_uuid,
)
from app.api.chat.session import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    SUPPORTED_COUNTRIES,
    create_session_cookie,
    decode_session_cookie,
    new_chat_id,
)
from app.domain.models.message import Channel, IncomingMessage, MessageType
from app.domain.services.conversation_service import ConversationService

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── helpers ──────────────────────────────────────────────────────────

def _conv_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def _whisper(request: Request) -> OpenAIWhisper:
    svc = getattr(request.app.state, "_whisper", None)
    if svc is None:
        svc = OpenAIWhisper()
        request.app.state._whisper = svc
    return svc


def _set_session_cookie(
    response: Response,
    chat_id: str,
    country: str | None,
    city: str | None = None,
    salon_id: str | None = None,
) -> None:
    from app.core.config import get_settings
    settings = get_settings()
    token = create_session_cookie(chat_id, country, city, salon_id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "development",
        path="/",
    )


def _get_or_create_session(request: Request) -> tuple[str, dict, bool]:
    """Returns (chat_id, session_data, is_new). session_data has country/city/salon_id."""
    sess = decode_session_cookie(request.cookies.get(SESSION_COOKIE))
    if sess:
        return sess["cid"], sess, False
    return new_chat_id(), {}, True


def _build_msg(
    chat_id: str, country: str, mtype: MessageType,
    salon_id: str | None = None,
    text: str | None = None, media_url: str | None = None, media_mime: str | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        channel=Channel.WEB,
        country=country,
        salon_id=salon_id,
        external_user_id=chat_id,
        external_chat_id=chat_id,
        message_type=mtype,
        text=text,
        media_url=media_url,
        media_mime=media_mime,
        received_at=datetime.now(timezone.utc),
        raw={},
    )


# ── pages ────────────────────────────────────────────────────────────

async def _fetch_salons(request: Request, country: str) -> list[dict]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await session.execute(sql_text(f'SET search_path TO "{country}", public'))
        rows = await session.execute(sql_text(
            "SELECT id, name, city, address, phone FROM salon "
            "WHERE archive=false ORDER BY city, name"
        ))
        return [
            {"id": r[0], "name": r[1], "city": r[2], "address": r[3], "phone": r[4]}
            for r in rows.all()
        ]


def _no_cache(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/chat", response_class=HTMLResponse)
async def chat_index(request: Request) -> Response:
    """Якщо в cookie є salon_id → /chat/c. Якщо country → /chat/salon. Інакше — start."""
    chat_id, sess, is_new = _get_or_create_session(request)
    if sess.get("salon_id"):
        resp: Response = RedirectResponse(url="/chat/c", status_code=302)
    elif sess.get("country"):
        resp = RedirectResponse(url="/chat/salon", status_code=302)
    else:
        resp = templates.TemplateResponse(request, "onboarding.html", {
            "countries": SUPPORTED_COUNTRIES,
        })
        _no_cache(resp)
    if is_new:
        _set_session_cookie(resp, chat_id, None)
    return resp


@router.get("/chat/restart", response_class=HTMLResponse)
async def chat_restart(request: Request) -> Response:
    """Скидає всі вибори в сесії (country/city/salon) і шле на онбординг."""
    chat_id, _, _ = _get_or_create_session(request)
    resp: Response = RedirectResponse(url="/chat", status_code=302)
    _set_session_cookie(resp, chat_id, None)
    return resp


@router.get("/chat/start")
async def chat_start(
    request: Request,
    salon_id: str | None = None,
    salon_slug: str | None = None,
    lang: str | None = None,
) -> Response:
    """Зовнішня точка входу для booking_london_predict (та інших каналів).

    Параметри (один з двох обовʼязковий):
      ?salon_id=<uuid>     — UUID салону з нашої PG
      ?salon_slug=<slug>   — booking-friendly slug (london-mortimer, kyiv-obolon, ...)

    Якщо salon відомий — створюємо НОВУ сесію (новий chat_id) з обраним
    salon_id/city/country, грієм cache і ведемо одразу в /chat/c.

    Якщо невідомий — 404 (не fallback на онбординг, щоб не маскувати bug у партнера).
    """
    info: dict[str, str] | None = None
    resolved_uuid: str | None = None
    if salon_id:
        info = lookup_by_uuid(salon_id)
        if info:
            resolved_uuid = salon_id
    elif salon_slug:
        found = lookup_by_slug(salon_slug)
        if found:
            resolved_uuid, info = found
    else:
        raise HTTPException(400, "salon_id або salon_slug обовʼязковий")

    if not info or not resolved_uuid:
        raise HTTPException(404, "невідомий салон")

    # Нова сесія — щоб клієнт точно почав чистий діалог з вибраним салоном.
    new_id = new_chat_id()
    asyncio.create_task(_warm_city_cache(request.app, info["country"], [resolved_uuid]))
    log.info(
        "[CHAT START] uuid=%s slug=%s country=%s city=%s name=%s",
        resolved_uuid, info["slug"], info["country"], info["city"], info["name"],
    )
    resp: Response = RedirectResponse(url="/chat/c", status_code=302)
    _set_session_cookie(resp, new_id, info["country"], city=info["city"], salon_id=resolved_uuid)
    return resp


@router.get("/chat/salon", response_class=HTMLResponse)
async def chat_salon(request: Request) -> Response:
    """ОДИН крок: показує всі салони країни, згруповані по містах."""
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    if not country:
        return RedirectResponse(url="/chat", status_code=302)
    salons = await _fetch_salons(request, country)
    if not salons:
        raise HTTPException(404, "no salons available")
    # Якщо в країні лише 1 салон — авто-вибір
    if len(salons) == 1:
        only = salons[0]
        asyncio.create_task(_warm_city_cache(request.app, country, [only["id"]]))
        resp: Response = RedirectResponse(url="/chat/c", status_code=302)
        _set_session_cookie(resp, chat_id, country, city=only["city"], salon_id=only["id"])
        return resp
    # Групуємо по містах
    cities_map: dict[str, list[dict]] = {}
    for s in salons:
        cities_map.setdefault(s["city"] or "—", []).append(s)
    cities = [{"name": c, "salons": cities_map[c]} for c in sorted(cities_map)]
    resp = templates.TemplateResponse(request, "salon.html", {
        "country": country,
        "cities": cities,
    })
    return _no_cache(resp)


@router.get("/chat/c", response_class=HTMLResponse)
async def chat_main(request: Request) -> Response:
    chat_id, sess, _ = _get_or_create_session(request)
    if not sess.get("country"):
        return RedirectResponse(url="/chat", status_code=302)
    if not sess.get("salon_id"):
        return RedirectResponse(url="/chat/salon", status_code=302)
    # Знаходимо salon name для UI
    all_salons = await _fetch_salons(request, sess["country"])
    salon = next((s for s in all_salons if s["id"] == sess["salon_id"]), None)
    resp = templates.TemplateResponse(request, "chat.html", {
        "country": sess["country"],
        "chat_id": chat_id,
        "city": sess.get("city"),
        "salon": salon,
    })
    return _no_cache(resp)


# ── onboarding ───────────────────────────────────────────────────────

@router.post("/api/chat/country")
async def set_country(request: Request, country: str = Form(...)) -> JSONResponse:
    c = country.lower().strip()
    if c not in SUPPORTED_COUNTRIES:
        raise HTTPException(400, "unsupported country")
    chat_id, _, _ = _get_or_create_session(request)
    resp = JSONResponse({"ok": True, "country": c, "redirect": "/chat/salon"})
    _set_session_cookie(resp, chat_id, c)
    return resp


async def _warm_city_cache(app, country: str, salon_ids: list[str]) -> None:
    """Фоново викликаємо популярні tools для всіх салонів міста — щоб коли клієнт
    почне діалог, агент брав з Redis cache замість живих DB+LLM запитів.
    Tools уже мають Redis caching (10-хв TTL для list_*/search_*)."""
    try:
        agent = app.state.conversation_service._agents.get_agent(country)  # noqa: SLF001
        registry = agent._tools  # noqa: SLF001
        warm_tasks = []
        for sid in salon_ids:
            # list_categories — швидкий, потрібен майже на кожен діалог
            warm_tasks.append(registry.execute("list_categories", {"salon_id": sid}))
            # популярні запити — search_services з широким query (триггерить vector search +
            # потрапляє у Redis cache на 10 хв)
            for q in ("манікюр", "стрижка", "фарбування", "масаж", "брови"):
                warm_tasks.append(registry.execute("search_services", {"salon_id": sid, "query": q, "limit": 10}))
        await asyncio.gather(*warm_tasks, return_exceptions=True)
        log.info("[WARM] city cache filled: country=%s salons=%d tasks=%d", country, len(salon_ids), len(warm_tasks))
    except Exception as exc:  # noqa: BLE001
        log.warning("warm cache failed: %s", exc)


@router.post("/api/chat/salon")
async def set_salon(request: Request, salon_id: str = Form(...)) -> JSONResponse:
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    if not country:
        raise HTTPException(400, "country not set")
    salons = await _fetch_salons(request, country)
    salon = next((s for s in salons if s["id"] == salon_id), None)
    if not salon:
        raise HTTPException(400, "unknown salon")
    # Background warm — салони того ж міста, що і обраний
    city_salon_ids = [s["id"] for s in salons if s["city"] == salon["city"]]
    asyncio.create_task(_warm_city_cache(request.app, country, city_salon_ids))
    resp = JSONResponse({"ok": True, "salon_id": salon_id, "redirect": "/chat/c"})
    _set_session_cookie(resp, chat_id, country, city=salon["city"], salon_id=salon_id)
    return resp


@router.get("/api/chat/salons")
async def list_salons_api(request: Request) -> JSONResponse:
    _, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    if not country:
        return JSONResponse({"salons": []})
    salons = await _fetch_salons(request, country)
    return JSONResponse({"salons": salons, "country": country})


# ── messaging ────────────────────────────────────────────────────────

TOOL_LABELS = {
    "list_salons": "Дивлюсь салони…",
    "list_categories": "Переглядаю каталог…",
    "search_services": "Шукаю відповідні послуги…",
    "search_by_concern": "Підбираю послугу під вашу потребу…",
    "find_masters_for_service": "Шукаю майстрів…",
    "get_available_slots": "Перевіряю вільні години…",
    "find_service_and_slots": "Шукаю послугу і час разом…",
    "create_booking": "Створюю запис…",
    "get_client_bookings": "Підіймаю ваші записи…",
    "cancel_booking": "Скасовую запис…",
}

# Пул фраз для рекомендаційного індикатора. Емітимо по черзі кожен heartbeat,
# щоб клієнт бачив що Mira продовжує думати — як заміна тексту у живій сесії.
FILLER_PHRASES = [
    "Аналізую запит…",
    "Підбираю кращий варіант…",
    "Перевіряю деталі…",
    "Уточнюю розклад…",
    "Готую відповідь…",
    "Майже завершила…",
]
HEARTBEAT_SECONDS = 4.0


@router.post("/api/chat/send-stream")
async def send_text_stream(request: Request, text: str = Form(...)) -> StreamingResponse:
    """SSE-варіант: емітить tool-progress подіїї, потім фінальний reply.
    Формат SSE: `data: {JSON}\\n\\n`. Тип подій:
      {"type":"tool","name":"search_services","label":"Шукаю послуги…"}
      {"type":"reply","text":"…"}
      {"type":"error","message":"…"}
      {"type":"done"}
    """
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    salon_id = sess.get("salon_id")
    if not country or not salon_id:
        raise HTTPException(400, "onboarding not complete")
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    if len(text) > 4000:
        raise HTTPException(400, "text too long")

    msg = _build_msg(chat_id, country, MessageType.TEXT, salon_id=salon_id, text=text)
    conv = _conv_service(request)
    queue: asyncio.Queue = asyncio.Queue()

    async def on_tool_event(phase: str, name: str) -> None:
        if phase == "start":
            await queue.put({
                "type": "tool",
                "name": name,
                "label": TOOL_LABELS.get(name, "Працюю…"),
            })

    async def producer() -> None:
        try:
            reply = await conv.process_web_turn([msg], on_tool_event=on_tool_event)
            await queue.put({"type": "reply", "text": reply})
        except Exception as exc:
            log.exception("send_text_stream error: %s", exc)
            await queue.put({"type": "error", "message": "Сталася помилка. Спробуйте ще раз."})
        finally:
            await queue.put({"type": "done"})

    async def event_stream():
        task = asyncio.create_task(producer())
        filler_idx = 0
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Замінюємо текст на наступну фразу з пулу — живий індикатор
                    label = FILLER_PHRASES[filler_idx % len(FILLER_PHRASES)]
                    yield f"data: {json.dumps({'type':'tool','name':'wait','label':label}, ensure_ascii=False)}\n\n"
                    filler_idx += 1
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev["type"] == "done":
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/chat/send")
async def send_text(request: Request, text: str = Form(...)) -> JSONResponse:
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    salon_id = sess.get("salon_id")
    if not country or not salon_id:
        raise HTTPException(400, "onboarding not complete")
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    if len(text) > 4000:
        raise HTTPException(400, "text too long")
    msg = _build_msg(chat_id, country, MessageType.TEXT, salon_id=salon_id, text=text)
    reply = await _conv_service(request).process_web_turn([msg])
    return JSONResponse({"reply": reply})


@router.post("/api/chat/voice")
async def send_voice(request: Request, audio: UploadFile = File(...)) -> JSONResponse:
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    salon_id = sess.get("salon_id")
    if not country or not salon_id:
        raise HTTPException(400, "onboarding not complete")
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "empty audio")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(400, "audio too large (max 25MB)")
    mime = audio.content_type or "audio/webm"
    try:
        transcript = await _whisper(request).transcribe(raw, mime)
    except Exception as exc:
        log.exception("whisper transcribe failed: %s", exc)
        raise HTTPException(502, "transcription failed") from exc
    transcript = (transcript or "").strip()
    if not transcript:
        return JSONResponse({"reply": "Вибачте, не вдалося розпізнати голос. Спробуйте ще раз або напишіть текстом.", "transcript": ""})
    msg = _build_msg(chat_id, country, MessageType.TEXT, salon_id=salon_id, text=transcript)
    reply = await _conv_service(request).process_web_turn([msg])
    return JSONResponse({"reply": reply, "transcript": transcript})


@router.post("/api/chat/image")
async def send_image(
    request: Request,
    image: UploadFile = File(...),
    caption: str = Form(""),
) -> JSONResponse:
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    salon_id = sess.get("salon_id")
    if not country or not salon_id:
        raise HTTPException(400, "onboarding not complete")
    raw = await image.read()
    if not raw:
        raise HTTPException(400, "empty image")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "image too large (max 10MB)")
    mime = (image.content_type or "image/jpeg").lower()
    if not mime.startswith("image/"):
        raise HTTPException(400, "not an image")
    b64 = base64.standard_b64encode(raw).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    msg = _build_msg(
        chat_id, country, MessageType.IMAGE,
        salon_id=salon_id,
        text=caption.strip() or None,
        media_url=data_url, media_mime=mime,
    )
    reply = await _conv_service(request).process_web_turn([msg])
    return JSONResponse({"reply": reply})


# ── history / reset ─────────────────────────────────────────────────

@router.get("/api/chat/history")
async def get_history(request: Request) -> JSONResponse:
    chat_id, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    if not country:
        return JSONResponse({"history": []})
    cache = request.app.state.conversation_service._cache  # noqa: SLF001
    conv_id = f"web:{country}:{chat_id}"
    conv = await cache.load(conv_id)
    if not conv:
        return JSONResponse({"history": []})
    items = [
        {"role": t.role, "content": t.content, "has_image": t.has_image,
         "at": t.created_at.isoformat() if t.created_at else None}
        for t in conv.history
    ]
    return JSONResponse({"history": items, "salon_id": conv.salon_id})


@router.post("/api/chat/reset")
async def reset_chat(request: Request) -> JSONResponse:
    """Новий діалог: новий chat_id (історія старого не зачіпається — просто йдемо в новий)."""
    _, sess, _ = _get_or_create_session(request)
    country = sess.get("country")
    city = sess.get("city")
    salon_id = sess.get("salon_id")
    new_id = new_chat_id()
    resp = JSONResponse({"ok": True, "chat_id": new_id})
    _set_session_cookie(resp, new_id, country, city=city, salon_id=salon_id)
    return resp
