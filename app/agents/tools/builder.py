"""Збирає ToolRegistry для конкретної країни.

Усі tools приймають salon_id у параметрах: агент спочатку отримує
список салонів через list_salons, уточнює у клієнта і далі викликає
tools з обраним salon_id.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.agents.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# Tools, результат яких можна кешувати безпечно.
# Услуги/категорії/салони/майстри — рідко змінюються (CRM sync 1×/добу).
# get_available_slots МАЄ КОРОТКИЙ TTL — слоти змінюються кожну хв.
CACHEABLE_TOOLS_LONG = ("list_salons", "list_categories", "search_services",
                        "search_by_concern", "find_masters_for_service")
CACHEABLE_TOOLS_SHORT = ("get_available_slots",)
LONG_TTL = 600   # 10 хв — для каталогу
SHORT_TTL = 45   # 45с — для слотів


def _cache_key(country: str, tool_name: str, args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    h = hashlib.sha256(f"{country}|{tool_name}|{payload}".encode("utf-8")).hexdigest()
    return f"tool:{tool_name}:{h}"
from app.infrastructure.db.models.scheduling import Booking
from app.infrastructure.db.repositories.booking_repo import BookingRepository
from app.infrastructure.db.repositories.client_repo import ClientRepository
from app.infrastructure.db.repositories.employee_repo import EmployeeRepository
from app.infrastructure.db.repositories.salon_repo import SalonRepository
from app.infrastructure.db.repositories.service_repo import (
    CategoryRepository,
    ServiceRepository,
)
from app.infrastructure.db.repositories.slot_repo import TimeSlotRepository
from app.infrastructure.db.session import country_session
from app.integrations.crm.sync_service import CRMBookingSync


def build_tool_registry(
    country: str,
    session_factory: async_sessionmaker[AsyncSession],
    embedder=None,
    query_rewriter=None,
    llm_reranker=None,
    redis: Redis | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    crm_sync = CRMBookingSync()

    # Перехоплюємо register() щоб обгорнути cacheable tools у Redis-кеш.
    _orig_register = registry.register

    def register_with_cache(schema: dict[str, Any], handler):
        name = schema["name"]
        ttl = LONG_TTL if name in CACHEABLE_TOOLS_LONG else (SHORT_TTL if name in CACHEABLE_TOOLS_SHORT else 0)
        if redis is None or ttl == 0:
            _orig_register(schema, handler)
            return

        async def cached_handler(args: dict[str, Any]) -> Any:
            key = _cache_key(country, name, args)
            try:
                raw = await redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("tool cache get %s failed: %s", name, exc)
            result = await handler(args)
            # Не кешуємо помилки
            if isinstance(result, dict) and result.get("error"):
                return result
            try:
                await redis.set(key, json.dumps(result, default=str, ensure_ascii=False), ex=ttl)
            except Exception as exc:  # noqa: BLE001
                log.warning("tool cache set %s failed: %s", name, exc)
            return result

        _orig_register(schema, cached_handler)

    registry.register = register_with_cache  # type: ignore[method-assign]

    async def with_session(fn):
        async with country_session(session_factory, country) as session:
            return await fn(session)

    # ──────────────────────────────────────────────────────────────
    # 1. list_salons — спочатку клієнт обирає салон
    # ──────────────────────────────────────────────────────────────
    async def _list_salons(args: dict[str, Any]) -> Any:
        async def _do(session):
            repo = SalonRepository(session)
            city = args.get("city")
            salons = await (repo.list_by_city(city) if city else repo.list_all())
            return [
                {"salon_id": s.id, "name": s.name, "city": s.city, "address": s.address, "phone": s.phone}
                for s in salons
            ]
        return await with_session(_do)

    registry.register(
        {
            "name": "list_salons",
            "description": (
                "Повертає список салонів країни. Викликай НА ПОЧАТКУ розмови, щоб клієнт обрав салон. "
                "Можна фільтрувати за містом."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Опційний фільтр за містом"},
                },
            },
        },
        _list_salons,
    )

    # ──────────────────────────────────────────────────────────────
    # 2. list_categories — категорії послуг конкретного салону
    # ──────────────────────────────────────────────────────────────
    async def _list_categories(args: dict[str, Any]) -> Any:
        async def _do(session):
            repo = CategoryRepository(session)
            parent_id = args.get("parent_id")
            cats = (
                await repo.list_children(args["salon_id"], parent_id)
                if parent_id
                else await repo.list_roots(args["salon_id"])
            )
            return [{"id": c.id, "name": c.name, "parent_id": c.parent_id} for c in cats]
        return await with_session(_do)

    registry.register(
        {
            "name": "list_categories",
            "description": "Повертає категорії послуг салону. Без parent_id — кореневі. З parent_id — підкатегорії.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string"},
                    "parent_id": {"type": "string"},
                },
                "required": ["salon_id"],
            },
        },
        _list_categories,
    )

    # ──────────────────────────────────────────────────────────────
    # 3. search_services
    # ──────────────────────────────────────────────────────────────
    async def _search_services(args: dict[str, Any]) -> Any:
        async def _do(session):
            repo = ServiceRepository(
                session,
                embedder=embedder,
                query_rewriter=query_rewriter,
                llm_reranker=llm_reranker,
            )
            services = await repo.search(
                salon_id=args["salon_id"],
                query=args.get("query"),
                category_id=args.get("category_id"),
                gender=args.get("gender"),
                brand=args.get("brand"),
                volume_ml=args.get("volume_ml"),
                zones=args.get("zones"),
                session_minutes=args.get("session_minutes"),
                ampules=args.get("ampules"),
                limit=args.get("limit", 15),
            )
            return [
                {
                    "service_id": s.id,
                    "name": {
                        "uk": s.name_uk or s.name,
                        "ru": s.name_ru or s.name,
                        "en": s.name_en or s.name,
                        "pl": s.name_pl or s.name,
                    },
                    "original_name": s.name,
                    "duration_min": s.duration_min,
                    "price": float(s.price),
                    "currency": s.price_currency,
                    "category": s.category.name if s.category else None,
                }
                for s in services
            ]
        return await with_session(_do)

    registry.register(
        {
            "name": "search_services",
            "description": (
                "Шукає послуги салону за вільним запитом і/або категорією. "
                "Використовуй коли клієнт описує що хоче (наприклад: 'манікюр з покриттям', 'фарбування волосся'). "
                "Фільтри brand/volume_ml/zones/session_minutes/ampules — для конкретики "
                "('Юведерм 2 мл', 'класичний масаж 90 хв', 'антицелюлітний масаж 2 зони')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string"},
                    "query": {"type": "string", "description": "Вільний пошуковий запит"},
                    "category_id": {"type": "string"},
                    "gender": {"type": "string", "enum": ["male", "female", "both"]},
                    "brand": {
                        "type": "string",
                        "description": "Бренд препарату/косметики (lebel, brae, juvederm, ...)",
                    },
                    "volume_ml": {"type": "number", "description": "Обʼєм у мл (1, 0.55, 2)"},
                    "zones": {"type": "integer", "description": "Кількість зон (для масажу/депіляції)"},
                    "session_minutes": {
                        "type": "integer",
                        "description": "Тривалість сесії у хвилинах (60, 90, 120)",
                    },
                    "ampules": {
                        "type": "integer",
                        "description": "Кількість ампул (0=без ампули, 1, 2)",
                    },
                    "limit": {"type": "integer", "default": 15},
                },
                "required": ["salon_id"],
            },
        },
        _search_services,
    )

    # ──────────────────────────────────────────────────────────────
    # 3.b. search_by_concern — пошук за описом проблеми/потреби клієнта
    # ──────────────────────────────────────────────────────────────
    async def _search_by_concern(args: dict[str, Any]) -> Any:
        from app.infrastructure.db.repositories.profile_repo import (
            ServiceProfileRepository,
        )

        async with session_factory() as session:
            try:
                repo = ServiceProfileRepository(session, embedder=embedder)

                # Expert mode: profiles for THIS salon first
                salon_id = args["salon_id"]
                results = await repo.search_by_concern_v2(
                    query=args["query"],
                    country=country,
                    salon_id=salon_id,
                    limit=args.get("limit", 5),
                    language=args.get("language", "uk"),
                )
                used_fallback_salons: list[str] = []
                if not results:
                    # Fallback: same-city salons
                    from sqlalchemy import text as sql_text
                    city_row = await session.execute(sql_text(
                        f"SELECT city FROM {country}.salon WHERE id = :sid"
                    ), {"sid": salon_id})
                    city = city_row.scalar()
                    if city:
                        sib_rows = await session.execute(sql_text(
                            f"SELECT id FROM {country}.salon "
                            f"WHERE city = :c AND id != :sid AND archive = false"
                        ), {"c": city, "sid": salon_id})
                        sibling_ids = [r[0] for r in sib_rows.all()]
                        for sid in sibling_ids:
                            sib_results = await repo.search_by_concern_v2(
                                query=args["query"], country=country, salon_id=sid,
                                limit=args.get("limit", 5),
                                language=args.get("language", "uk"),
                            )
                            if sib_results:
                                results = sib_results
                                used_fallback_salons = [sid]
                                break

                # Збагачуємо: для кожного result знаходимо конкретні послуги в салоні
                from sqlalchemy import text as sql_text
                response = []
                for item in results:
                    # Стабільний lookup: через profile.id → service.profile_id
                    # (не через canonical_keys — ті можуть мінятись при перейменуванні CRM)
                    target_salon = used_fallback_salons[0] if used_fallback_salons else args["salon_id"]
                    profile_id = item.get("category_id")
                    if not profile_id:
                        continue

                    all_services = []
                    sql = sql_text(
                        f"""
                        SELECT id, name, name_uk, name_ru, name_en, name_pl,
                               duration_min, price, price_currency
                        FROM {country}.service
                        WHERE profile_id = :pid
                          AND salon_id = :salon_id
                          AND archive = false
                        ORDER BY price
                        LIMIT 10
                        """
                    )
                    rows = await session.execute(
                        sql, {"pid": str(profile_id), "salon_id": target_salon}
                    )
                    for r in rows.fetchall():
                        all_services.append({
                            "service_id": r[0],
                            "name": {
                                "uk": r[2] or r[1],
                                "ru": r[3] or r[1],
                                "en": r[4] or r[1],
                                "pl": r[5] or r[1],
                            },
                            "duration_min": r[6],
                            "price": float(r[7]) if r[7] else 0,
                            "currency": r[8],
                        })

                    if not all_services:
                        continue

                    response.append({
                        "category": item["category"],
                        "option_name": item.get("option_name"),
                        "option_type": item.get("option_type"),
                        "score": item["score"],
                        "short_description": item["short_description"],
                        "addresses_problems": item.get("addresses_problems", []),
                        "benefits": item.get("benefits", []),
                        "when_to_recommend": item.get("when_to_recommend"),
                        "sales_pitch": item.get("sales_pitch"),
                        "cross_sell": item.get("cross_sell", []),
                        "matched_services": all_services,
                        "in_other_salon": bool(used_fallback_salons),
                        "other_salon_id": used_fallback_salons[0] if used_fallback_salons else None,
                        # Per-canonical_key context — допомагає AI обрати конкретний варіант
                        "key_descriptions": item.get("key_descriptions", {}),
                    })
                return response
            finally:
                await session.commit()

    registry.register(
        {
            "name": "search_by_concern",
            "description": (
                "Шукає послуги за описом ПРОБЛЕМИ або БАЖАНОГО РЕЗУЛЬТАТУ клієнта "
                "(наприклад: 'хочу щоб волосся сяяло', 'часто облазить лак', "
                "'у мене груба шкіра на стопах', 'не хочу нарощування але хочу довгі вії'). "
                "Повертає послуги З ОПИСОМ що вони вирішують + готовий sales pitch. "
                "Використовуй коли клієнт говорить про потребу/результат, не про конкретну послугу. "
                "Якщо клієнт називає послугу прямо ('хочу манікюр') — використовуй search_services."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string"},
                    "query": {
                        "type": "string",
                        "description": "Опис проблеми або бажаного результату клієнта",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["uk", "ru", "en", "pl"],
                        "default": "uk",
                    },
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["salon_id", "query"],
            },
        },
        _search_by_concern,
    )

    # ──────────────────────────────────────────────────────────────
    # 4. find_masters_for_service
    # ──────────────────────────────────────────────────────────────
    async def _find_masters(args: dict[str, Any]) -> Any:
        async def _do(session):
            repo = EmployeeRepository(session)
            masters = await repo.find_for_service(args["service_id"])
            return [{"master_id": m.id, "name": m.name, "title": m.title} for m in masters]
        return await with_session(_do)

    registry.register(
        {
            "name": "find_masters_for_service",
            "description": "Повертає майстрів салону, які виконують конкретну послугу.",
            "input_schema": {
                "type": "object",
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        },
        _find_masters,
    )

    # ──────────────────────────────────────────────────────────────
    # 5. get_available_slots
    # ──────────────────────────────────────────────────────────────
    async def _get_slots(args: dict[str, Any]) -> Any:
        async def _do(session):
            svc_repo = ServiceRepository(session)
            emp_repo = EmployeeRepository(session)
            slot_repo = TimeSlotRepository(session)

            service = await svc_repo.get_by_id(args["service_id"])
            if not service:
                return {"error": "service not found"}

            date_from = datetime.fromisoformat(args["date_from"])
            date_to = datetime.fromisoformat(args.get("date_to") or (date_from + timedelta(days=7)).isoformat())

            master_ids: list[str]
            if args.get("master_id"):
                master_ids = [args["master_id"]]
            else:
                masters = await emp_repo.find_for_service(service.id)
                master_ids = [m.id for m in masters]

            results: list[dict] = []
            for mid in master_ids:
                windows = await slot_repo.find_free_windows(
                    employee_id=mid,
                    date_from=date_from,
                    date_to=date_to,
                    duration_min=service.duration_min,
                    max_results=args.get("limit", 5),
                )
                emp = await emp_repo.get_by_id(mid)
                for w in windows:
                    results.append({
                        "master_id": mid,
                        "master_name": emp.name if emp else None,
                        "start_at": w["start_at"].isoformat(),
                        "end_at": w["end_at"].isoformat(),
                        "slot_ids": w["slot_ids"],
                    })
            results.sort(key=lambda x: x["start_at"])
            return results[: args.get("limit", 20)]
        return await with_session(_do)

    registry.register(
        {
            "name": "get_available_slots",
            "description": (
                "Повертає вільні часові вікна для послуги. Якщо master_id не вказано — "
                "шукає по всіх майстрах, які виконують цю послугу. Дати — у форматі ISO (YYYY-MM-DD або YYYY-MM-DDTHH:MM:SS)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "master_id": {"type": "string"},
                    "date_from": {"type": "string", "description": "ISO дата/час"},
                    "date_to": {"type": "string", "description": "ISO дата/час, опційно"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["service_id", "date_from"],
            },
        },
        _get_slots,
    )

    # ──────────────────────────────────────────────────────────────
    # 5.b. find_service_and_slots — комбінований tool для booking flow
    # Об'єднує search → masters → slots в один виклик. ПЕРЕВАГА КОЛИ
    # клієнт вже знає чого хоче і коли. Зменшує tool-loop iterations ~3→1.
    # ──────────────────────────────────────────────────────────────
    async def _find_service_and_slots(args: dict[str, Any]) -> Any:
        from app.infrastructure.db.repositories.profile_repo import (
            ServiceProfileRepository,
        )
        async with session_factory() as session:
            try:
                from sqlalchemy import text as sql_text
                await session.execute(sql_text(f'SET search_path TO "{country}", public'))

                svc_repo = ServiceRepository(
                    session, embedder=embedder,
                    query_rewriter=query_rewriter, llm_reranker=llm_reranker,
                )
                emp_repo = EmployeeRepository(session)
                slot_repo = TimeSlotRepository(session)

                salon_id = args["salon_id"]
                query = args["query"]

                # 1. Шукаємо послуги. Спочатку search_by_concern — якщо є інтент-опис.
                # Якщо не знайшло — fallback на search_services.
                profile_repo = ServiceProfileRepository(session, embedder=embedder)
                concern_results = await profile_repo.search_by_concern_v2(
                    query=query, country=country, salon_id=salon_id,
                    limit=3, language=args.get("language", "uk"),
                )

                service_candidates: list[Any] = []
                if concern_results:
                    profile_id = concern_results[0].get("category_id")
                    if profile_id:
                        rows = await session.execute(sql_text(
                            f"""SELECT id FROM {country}.service
                                WHERE profile_id = :pid AND salon_id = :sid AND archive = false
                                ORDER BY price LIMIT 5"""
                        ), {"pid": str(profile_id), "sid": salon_id})
                        ids = [r[0] for r in rows.fetchall()]
                        for sid in ids:
                            svc = await svc_repo.get_by_id(sid)
                            if svc:
                                service_candidates.append(svc)

                if not service_candidates:
                    service_candidates = await svc_repo.search(
                        salon_id=salon_id, query=query, limit=5,
                    )

                if not service_candidates:
                    return {"error": "no services matched"}

                # 2. Дати
                date_from = datetime.fromisoformat(args["date_from"]) if args.get("date_from") \
                    else datetime.now(timezone.utc)
                date_to = datetime.fromisoformat(args["date_to"]) if args.get("date_to") \
                    else date_from + timedelta(days=7)
                max_slots = args.get("slots_per_service", 3)

                # 3. Для кожного кандидата — майстри + слоти
                out: list[dict] = []
                for svc in service_candidates[: args.get("services_limit", 2)]:
                    masters = await emp_repo.find_for_service(svc.id)
                    master_slots: list[dict] = []
                    for m in masters:
                        windows = await slot_repo.find_free_windows(
                            employee_id=m.id,
                            date_from=date_from,
                            date_to=date_to,
                            duration_min=svc.duration_min,
                            max_results=max_slots,
                        )
                        for w in windows:
                            master_slots.append({
                                "master_id": m.id,
                                "master_name": m.name,
                                "start_at": w["start_at"].isoformat(),
                                "end_at": w["end_at"].isoformat(),
                                "slot_ids": w["slot_ids"],
                            })
                    master_slots.sort(key=lambda x: x["start_at"])
                    out.append({
                        "service_id": svc.id,
                        "name": {
                            "uk": svc.name_uk or svc.name,
                            "en": svc.name_en or svc.name,
                            "pl": svc.name_pl or svc.name,
                        },
                        "duration_min": svc.duration_min,
                        "price": float(svc.price),
                        "currency": svc.price_currency,
                        "available_slots": master_slots[: args.get("slots_total_limit", 10)],
                    })
                return out
            finally:
                await session.commit()

    registry.register(
        {
            "name": "find_service_and_slots",
            "description": (
                "ПРЕФЕРОВАНИЙ TOOL для booking flow коли клієнт описав і потребу/послугу, і дату. "
                "Об'єднує пошук послуг + майстрів + вільних слотів — заміна 3 окремих викликів "
                "(search_services + find_masters + get_available_slots) одним. "
                "Викликай коли є: salon_id + query (опис послуги/потреби) + дата. "
                "Повертає до 2 послуг-кандидатів зі слотами кожного майстра."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string"},
                    "query": {"type": "string", "description": "Опис послуги або потреби клієнта"},
                    "date_from": {"type": "string", "description": "ISO дата/час від"},
                    "date_to": {"type": "string", "description": "ISO дата/час до (опц.)"},
                    "services_limit": {"type": "integer", "default": 2, "description": "К-сть послуг-кандидатів"},
                    "slots_per_service": {"type": "integer", "default": 3, "description": "К-сть слотів на майстра"},
                    "slots_total_limit": {"type": "integer", "default": 10},
                    "language": {"type": "string", "enum": ["uk", "ru", "en", "pl"], "default": "uk"},
                },
                "required": ["salon_id", "query", "date_from"],
            },
        },
        _find_service_and_slots,
    )

    # ──────────────────────────────────────────────────────────────
    # 6. create_booking
    # ──────────────────────────────────────────────────────────────
    async def _create_booking(args: dict[str, Any]) -> Any:
        async def _do(session):
            svc_repo = ServiceRepository(session)
            client_repo = ClientRepository(session)
            booking_repo = BookingRepository(session)
            slot_repo = TimeSlotRepository(session)

            service = await svc_repo.get_by_id(args["service_id"])
            if not service:
                return {"error": "service not found"}

            start_at = datetime.fromisoformat(args["start_at"])
            end_at = datetime.fromisoformat(args["end_at"])

            # SECURITY BOUNDARY: переконуємось що slot_ids валідні і
            # реально походять з get_available_slots. Захищає від:
            # - порожніх/вигаданих slot_ids
            # - агента що пропустив виклик get_available_slots
            # - застарілих slot_ids (вже зайнятих)
            # - mismatch часу (агент скопіював не той варіант)
            slot_check = await slot_repo.validate_slots(
                slot_ids=args.get("slot_ids", []),
                employee_id=args["master_id"],
                start_at=start_at,
                end_at=end_at,
            )
            if not slot_check.get("ok"):
                return {"error": slot_check.get("error", "invalid slots")}

            client = await client_repo.get_or_create_by_external(
                channel=args["channel"],
                external_id=args["external_user_id"],
                name=args.get("client_name"),
            )
            if args.get("client_phone"):
                await client_repo.update_phone(client.id, args["client_phone"])

            booking = await booking_repo.create(
                client_id=client.id,
                employee_id=args["master_id"],
                service_id=service.id,
                salon_id=args["salon_id"],
                start_at=start_at,
                end_at=end_at,
                source_channel=args["channel"],
                source_chat_id=args.get("external_chat_id"),
                notes=args.get("notes"),
            )
            await slot_repo.mark_booked(args["slot_ids"], booking.id)
            return booking

        booking = await with_session(_do)
        if isinstance(booking, dict):  # помилка
            return booking

        # CRM push (no-op якщо CRM_PUSH_ENABLED=false)
        crm_id, crm_status = await crm_sync.push_booking(
            booking,
            client_phone=args["client_phone"],
            client_name=args["client_name"],
        )
        if crm_id:
            async def _save_crm(session):
                b = await session.get(Booking, booking.id)
                if b:
                    b.crm_id = crm_id
                    b.synced_to_crm_at = datetime.now(timezone.utc)
            await with_session(_save_crm)

        return {
            "booking_id": booking.id,
            "status": booking.status.value,
            "start_at": booking.start_at.isoformat(),
            "end_at": booking.end_at.isoformat(),
            "crm_status": crm_status,
            "crm_id": crm_id,
        }

    registry.register(
        {
            "name": "create_booking",
            "description": (
                "Створює запис після підтвердження клієнтом. ОБОВ'ЯЗКОВО спочатку отримай "
                "ім'я і телефон клієнта. slot_ids беруться з результату get_available_slots."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string"},
                    "service_id": {"type": "string"},
                    "master_id": {"type": "string"},
                    "start_at": {"type": "string"},
                    "end_at": {"type": "string"},
                    "slot_ids": {"type": "array", "items": {"type": "integer"}},
                    "channel": {"type": "string"},
                    "external_user_id": {"type": "string"},
                    "external_chat_id": {"type": "string"},
                    "client_name": {"type": "string"},
                    "client_phone": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "salon_id", "service_id", "master_id",
                    "start_at", "end_at", "slot_ids",
                    "channel", "external_user_id", "client_name", "client_phone",
                ],
            },
        },
        _create_booking,
    )

    # ──────────────────────────────────────────────────────────────
    # 7. get_client_bookings
    # ──────────────────────────────────────────────────────────────
    async def _get_client_bookings(args: dict[str, Any]) -> Any:
        async def _do(session):
            client_repo = ClientRepository(session)
            booking_repo = BookingRepository(session)
            client = await client_repo.get_or_create_by_external(
                channel=args["channel"], external_id=args["external_user_id"]
            )
            bookings = await booking_repo.list_for_client(client.id)
            return [
                {
                    "booking_id": b.id,
                    "service_id": b.service_id,
                    "master_id": b.employee_id,
                    "start_at": b.start_at.isoformat(),
                    "end_at": b.end_at.isoformat(),
                    "status": b.status.value,
                }
                for b in bookings
            ]
        return await with_session(_do)

    registry.register(
        {
            "name": "get_client_bookings",
            "description": "Повертає активні бронювання клієнта.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "external_user_id": {"type": "string"},
                },
                "required": ["channel", "external_user_id"],
            },
        },
        _get_client_bookings,
    )

    # ──────────────────────────────────────────────────────────────
    # 8. cancel_booking
    # ──────────────────────────────────────────────────────────────
    async def _cancel_booking(args: dict[str, Any]) -> Any:
        async def _do(session):
            booking_repo = BookingRepository(session)
            slot_repo = TimeSlotRepository(session)
            booking = await booking_repo.cancel(args["booking_id"])
            if not booking:
                return None
            await slot_repo.release(booking.id)
            return booking

        booking = await with_session(_do)
        if booking is None:
            return {"error": "booking not found"}

        crm_status = await crm_sync.cancel_in_crm(booking.crm_id, booking.salon_id)
        return {
            "booking_id": booking.id,
            "status": booking.status.value,
            "crm_status": crm_status,
        }

    registry.register(
        {
            "name": "cancel_booking",
            "description": "Скасовує запис і звільняє слоти. Перед викликом — підтвердження клієнта.",
            "input_schema": {
                "type": "object",
                "properties": {"booking_id": {"type": "integer"}},
                "required": ["booking_id"],
            },
        },
        _cancel_booking,
    )

    return registry
