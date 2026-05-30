"""Синхронізація даних з CRM (api.aihelps.com) у нашу БД.

Для кожного салону:
  1. categories
  2. services (+ автоматичне виведення positions, бо CRM повертає position_id у services)
  3. employees (+ employee_position M:N)
  4. products + product_categories
  5. freetime → time_slot

Перед інсертом — DELETE усіх старих даних по цьому salon_id (catalog/staff/slots).
clients і bookings/orders НЕ чіпає (вони мають свій життєвий цикл).

Запуск:
  python -m scripts.sync_from_crm --all
  python -m scripts.sync_from_crm --country ua
  python -m scripts.sync_from_crm --salon 776611              # за database_code
  python -m scripts.sync_from_crm --all --entities services,freetime
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, select

from app.infrastructure.db.models.catalog import (
    Category,
    Position,
    Product,
    ProductCategory,
    Service,
)
from app.infrastructure.db.models.scheduling import TimeSlot
from app.infrastructure.db.models.staff import Employee, EmployeePosition
from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.domain.services.canonical_key import extract_attributes, make_canonical_key
from app.infrastructure.db.session import build_engine, build_session_factory, country_session
from app.integrations.crm.client import CRMClient
from app.integrations.crm.salons_catalog import (
    SALONS,
    SalonConfig,
    by_country,
    by_database_code,
)

ALL_ENTITIES = ("categories", "services", "employees", "products", "freetime")
HTML_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
HTML_TAG = re.compile(r"<.*?>")


def ns(salon_id: str, ext_id: str) -> str:
    return f"{salon_id}:{ext_id}"


def clean_html(text: str | None) -> str:
    if not text:
        return ""
    text = HTML_BR.sub("\n", text)
    return HTML_TAG.sub("", text).strip()


def is_addon(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return "add-on" in n or "addon" in n


# Категорії, які не потрібні AI-чату (внутрішні + не-послуги)
_EXCLUDED_CAT_PATTERNS = (
    "add-on", "add on", "addon",
    "додатков", "дополнительн",
    "чайов", "чаев",
    "ваучер", "voucher",
    "подарунк", "подарочн", "gift card",
    "абонемент", "subscription",
)


def is_excluded_category_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(p in n for p in _EXCLUDED_CAT_PATTERNS)


# ──────────────────────────────────────────────────────────────────
# Cleanup перед синком
# ──────────────────────────────────────────────────────────────────
async def cleanup_salon(session, salon_id: str, entities: set[str]) -> None:
    """Видаляє old дані по салону у відповідних таблицях.

    Порядок важливий через FK. clients / bookings / product_orders НЕ чіпаємо.
    """
    salon_emp_ids = select(Employee.id).where(Employee.salon_id == salon_id).scalar_subquery()

    if "freetime" in entities:
        # time_slot прив'язаний до employee — видаляємо тільки незабронені
        await session.execute(
            delete(TimeSlot).where(
                TimeSlot.is_booked.is_(False),
                TimeSlot.employee_id.in_(salon_emp_ids),
            )
        )

    if "employees" in entities:
        # Спочатку видаляємо всі time_slot цих майстрів (інакше FK)
        await session.execute(
            delete(TimeSlot).where(TimeSlot.employee_id.in_(salon_emp_ids))
        )
        await session.execute(
            delete(EmployeePosition).where(EmployeePosition.employee_id.in_(salon_emp_ids))
        )
        # Видаляти employees можна лише якщо немає посилань з booking
        # → робимо archive=True замість delete для безпеки на майбутнє,
        # АЛЕ для MVP — DELETE (на старті booking немає)
        await session.execute(delete(Employee).where(Employee.salon_id == salon_id))

    if "services" in entities:
        await session.execute(delete(Service).where(Service.salon_id == salon_id))
        await session.execute(delete(Position).where(Position.salon_id == salon_id))

    if "categories" in entities:
        await session.execute(delete(Category).where(Category.salon_id == salon_id))

    if "products" in entities:
        await session.execute(delete(Product).where(Product.salon_id == salon_id))
        await session.execute(delete(ProductCategory).where(ProductCategory.salon_id == salon_id))

    await session.flush()


# ──────────────────────────────────────────────────────────────────
# Імпортери
# ──────────────────────────────────────────────────────────────────
async def import_categories(session, salon_id: str, raw: list[dict]) -> int:
    """Двопрохідний імпорт: спочатку всі без parent, потім UPDATE з parent.

    Виключає add-on/чайові/ваучери/подарунки/абонементи + усіх їхніх descendants.
    """
    # Build name lookup для виявлення excluded по name + ієрархії
    by_id = {c["id"]: c for c in raw if not c.get("archive")}

    def is_excluded_recursive(cat_id: str, visited: set[str] | None = None) -> bool:
        visited = visited or set()
        if cat_id in visited:
            return False
        visited.add(cat_id)
        c = by_id.get(cat_id)
        if not c:
            return False
        if is_excluded_category_name(c.get("name")):
            return True
        parent = c.get("parent")
        if parent:
            return is_excluded_recursive(parent, visited)
        return False

    excluded_crm_ids = {cid for cid in by_id if is_excluded_recursive(cid)}

    pending_parents: list[tuple[str, str]] = []
    count = 0
    for c in raw:
        if c.get("archive"):
            continue
        if c["id"] in excluded_crm_ids:
            continue
        cid = ns(salon_id, c["id"])
        session.add(Category(
            id=cid,
            salon_id=salon_id,
            name=c.get("name", ""),
            parent_id=None,
            picture=c.get("picture"),
            archive=False,
        ))
        if c.get("parent") and c["parent"] not in excluded_crm_ids:
            pending_parents.append((cid, ns(salon_id, c["parent"])))
        count += 1
    await session.flush()

    for child_id, parent_id in pending_parents:
        cat = await session.get(Category, child_id)
        if cat:
            cat.parent_id = parent_id
    await session.flush()
    return count


async def import_positions_and_services(
    session, salon_id: str, services_raw: list[dict], employees_raw: list[dict],
) -> tuple[int, int, int]:
    """Витягує positions з services + employees, потім імпортує services.

    Сервіси з посиланням на неімпортовану категорію (наприклад, архівну в CRM)
    скіпаються, щоб не падати на FK violation.

    Embedding робиться окремим проходом після синку (embed_missing_after_sync).

    Повертає (positions_count, services_count, services_skipped_missing_category).
    """
    # 1. Збираємо positions
    position_names: dict[str, str] = {}
    for emp in employees_raw:
        for pid, pname in zip(emp.get("positions") or [], emp.get("position_names") or []):
            position_names[pid] = pname
    for s in services_raw:
        pid = s.get("position")
        if pid and pid not in position_names:
            position_names[pid] = "Unknown"

    for ext_pid, name in position_names.items():
        session.add(Position(
            id=ns(salon_id, ext_pid),
            salon_id=salon_id,
            name=name,
            archive=False,
        ))
    await session.flush()

    # 2. Services. Ціна і position беруться з першого елемента location_prices.
    # Спочатку соберемо ще positions, які могли з'явитися з location_prices.services.
    extra_positions: dict[str, str] = {}
    for s in services_raw:
        for lp in s.get("location_prices") or []:
            if isinstance(lp, dict) and lp.get("position"):
                extra_positions.setdefault(lp["position"], "Unknown")
    new_pos = {pid: name for pid, name in extra_positions.items() if pid not in position_names}
    for ext_pid, name in new_pos.items():
        session.add(Position(id=ns(salon_id, ext_pid), salon_id=salon_id, name=name, archive=False))
        position_names[ext_pid] = name
    if new_pos:
        await session.flush()

    # Які category_id ми реально імпортували для цього салону.
    # CRM може повертати сервіси з посиланням на архівні / видалені категорії —
    # такі сервіси пропускаємо, інакше FK падає й уся транзакція по салону відкочується.
    existing_cat_ids = {
        row[0]
        for row in (
            await session.execute(select(Category.id).where(Category.salon_id == salon_id))
        ).all()
    }

    seen: set[str] = set()
    count = 0
    skipped_missing_cat = 0
    for s in services_raw:
        if s.get("archive") or s["id"] in seen or is_addon(s.get("name")):
            continue
        seen.add(s["id"])

        # Витягуємо price + position з location_prices
        lp_item: dict | None = None
        for lp in s.get("location_prices") or []:
            if isinstance(lp, dict):
                lp_item = lp
                break
        price = (lp_item or {}).get("price") or 0
        ext_position = (lp_item or {}).get("position")

        if not s.get("category") or not ext_position:
            continue

        cat_id = ns(salon_id, s["category"])
        if cat_id not in existing_cat_ids:
            skipped_missing_cat += 1
            continue

        # Витягуємо canonical_key + структуровані атрибути з назви
        raw_name = s.get("name", "")
        attrs = extract_attributes(raw_name)

        session.add(Service(
            id=ns(salon_id, s["id"]),
            salon_id=salon_id,
            name=raw_name,
            description=clean_html(s.get("description")),
            description_plain=clean_html(s.get("description")),
            duration_min=int(s.get("duration") or 0),
            price=price,
            price_currency=s.get("price_currency") or "USD",
            gender=s.get("gender") or "both",
            category_id=cat_id,
            position_id=ns(salon_id, ext_position),
            archive=False,
            canonical_key=make_canonical_key(attrs),
            brand=attrs["brand"],
            volume_ml=attrs["volume_ml"],
            zones=attrs["zones"],
            session_minutes=attrs["session_minutes"],
            ampules=attrs["ampules"],
        ))
        count += 1
    await session.flush()

    return len(position_names), count, skipped_missing_cat


async def import_employees(session, salon_id: str, raw: list[dict]) -> int:
    seen: set[str] = set()
    count = 0
    for e in raw:
        if e.get("archive") or e["id"] in seen:
            continue
        # Виключаємо службові
        if "співробітники" in (e.get("name") or "").casefold():
            continue
        seen.add(e["id"])

        emp_id = ns(salon_id, e["id"])
        session.add(Employee(
            id=emp_id,
            salon_id=salon_id,
            name=e.get("name", ""),
            title=e.get("title"),
            phones=e.get("phone"),
            emails=e.get("email"),
            roles=e.get("roles"),
            comments=e.get("commentsPlainText") or e.get("comments"),
            prepayment_required=bool(e.get("prepaymentRequired")),
            archive=False,
        ))
        for ext_pid in e.get("positions") or []:
            session.add(EmployeePosition(
                employee_id=emp_id,
                position_id=ns(salon_id, ext_pid),
            ))
        count += 1
    await session.flush()
    return count


async def import_products(
    session, salon_id: str, products_raw: list[dict], categories_raw: list[dict]
) -> tuple[int, int]:
    cat_count = 0
    for c in categories_raw or []:
        if c.get("archive"):
            continue
        session.add(ProductCategory(
            id=ns(salon_id, c["id"]),
            salon_id=salon_id,
            name=c.get("name") or "Unknown",
            archive=False,
        ))
        cat_count += 1
    await session.flush()

    seen: set[str] = set()
    count = 0
    for p in products_raw:
        if p.get("archive") or p["id"] in seen:
            continue
        seen.add(p["id"])

        # Ціна для роздрібу: products з CRM не завжди мають пряме price.
        # У поточних даних бачимо лише supply_price + price (з location_prices). Беремо що є.
        price = p.get("price") or p.get("supply_price") or 0
        currency = p.get("supply_price_currency") or "USD"

        session.add(Product(
            id=ns(salon_id, p["id"]),
            salon_id=salon_id,
            name=p.get("name", ""),
            description=p.get("description"),
            barcode=p.get("barcode"),
            vendor_code=p.get("vendor_code"),
            units=p.get("units") or "pcs",
            price=price,
            price_currency=currency,
            category_id=ns(salon_id, p["category"]) if p.get("category") else None,
            archive=False,
        ))
        count += 1
    await session.flush()
    return cat_count, count


async def import_freetime(session, salon_id: str, raw: dict) -> int:
    """raw = {employee_ext_id: {date: ['HH:MM', ...]}}.

    CRM повертає freetime навіть для архівних/виключених майстрів. Пропускаємо тих,
    кого немає в нашій employee після імпорту.
    """
    # Підвантажуємо існуючі employee_id для салону
    existing_ids = {
        row[0]
        for row in (
            await session.execute(
                select(Employee.id).where(Employee.salon_id == salon_id)
            )
        ).all()
    }

    count = 0
    skipped = 0
    for ext_emp_id, days in (raw or {}).items():
        emp_id = ns(salon_id, ext_emp_id)
        if emp_id not in existing_ids:
            skipped += 1
            continue
        for date_str, times in days.items():
            for hhmm in times:
                try:
                    dt = datetime.fromisoformat(f"{date_str}T{hhmm}:00")
                except ValueError:
                    continue
                session.add(TimeSlot(
                    employee_id=emp_id,
                    slot_at=dt,
                    duration_min=15,
                    is_booked=False,
                ))
                count += 1
    if skipped:
        print(f"  ⚠ skipped {skipped} freetime entries for unknown employees")
    await session.flush()
    return count


# ──────────────────────────────────────────────────────────────────
# Sync для одного салону
# ──────────────────────────────────────────────────────────────────
def _active_employee_ext_ids(employees_raw: list[dict]) -> set[str]:
    """Зовнішні id співробітників, яких ми реально імпортуємо (не архівні / не службові)."""
    active: set[str] = set()
    for e in employees_raw or []:
        if e.get("archive"):
            continue
        if "співробітники" in (e.get("name") or "").casefold():
            continue
        if e.get("id"):
            active.add(e["id"])
    return active


async def sync_salon(
    salon: SalonConfig, entities: set[str], factory
) -> None:
    print(f"\n=== {salon.country.upper()} | {salon.city} | {salon.name} ({salon.database_code}) ===")
    crm = CRMClient(salon.database_code)

    try:
        await crm.authenticate()

        # Тягнемо з CRM (паралельно те, що незалежне)
        tasks: dict[str, Any] = {}
        if "categories" in entities:
            tasks["categories"] = crm.get_categories()
        if "services" in entities or "employees" in entities:
            # services і employees потрібні разом (positions виводимо з обох)
            tasks["services"] = crm.get_services()
            tasks["employees"] = crm.get_employees()
        if "products" in entities:
            tasks["products"] = crm.get_products()
            tasks["product_categories"] = crm.get_product_categories()
        if "freetime" in entities:
            from app.integrations.crm.client import DEFAULT_FREETIME_LOCATION_ID
            today = datetime.utcnow().date()
            location = (
                salon.location_sales
                or await crm.get_primary_location_id()
                or DEFAULT_FREETIME_LOCATION_ID
            )
            tasks["freetime"] = crm.get_freetime(
                date_from=today.isoformat(),
                date_to=(today + timedelta(days=60)).isoformat(),
                location=location,
            )

        results = dict(zip(tasks.keys(), await asyncio.gather(*tasks.values(), return_exceptions=True)))

        # Перевірка помилок
        for name, val in results.items():
            if isinstance(val, Exception):
                print(f"  ✖ {name}: {val}")
                return

        async with country_session(factory, salon.country) as session:
            await cleanup_salon(session, salon.salon_id, entities)

            if "categories" in entities:
                n = await import_categories(session, salon.salon_id, results["categories"] or [])
                print(f"  ✓ categories: {n}")

            # Services + Positions (виведені з services + employees)
            if "services" in entities:
                pos_n, svc_n, svc_skip = await import_positions_and_services(
                    session,
                    salon.salon_id,
                    results.get("services") or [],
                    results.get("employees") or [],
                )
                print(f"  ✓ positions: {pos_n}")
                print(f"  ✓ services: {svc_n}")
                if svc_skip:
                    print(f"  ⚠ services skipped (missing category in CRM): {svc_skip}")

            if "employees" in entities:
                n = await import_employees(session, salon.salon_id, results.get("employees") or [])
                print(f"  ✓ employees: {n}")

            if "products" in entities:
                pc, pn = await import_products(
                    session,
                    salon.salon_id,
                    results.get("products") or [],
                    results.get("product_categories") or [],
                )
                print(f"  ✓ product_categories: {pc}")
                print(f"  ✓ products: {pn}")

            if "freetime" in entities:
                # CRM повертає freetime навіть для архівних / службових співробітників —
                # дропаємо їх ще до запису, щоб не плодити warnings і не вантажити БД.
                ft_raw = results.get("freetime") or {}
                active_ids = _active_employee_ext_ids(results.get("employees") or [])
                if active_ids:
                    dropped = sum(1 for k in ft_raw if k not in active_ids)
                    ft_raw = {k: v for k, v in ft_raw.items() if k in active_ids}
                    if dropped:
                        print(f"  ⚠ freetime dropped for archived/excluded employees: {dropped}")
                n = await import_freetime(session, salon.salon_id, ft_raw)
                print(f"  ✓ time_slots: {n}")
    finally:
        await crm.close()


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────
def select_salons(args) -> Iterable[SalonConfig]:
    if args.salon:
        s = by_database_code(args.salon)
        if not s:
            raise SystemExit(f"Salon with database_code {args.salon} not found")
        return [s]
    if args.country:
        return by_country(args.country)
    if args.all:
        return SALONS
    raise SystemExit("Specify --all, --country, or --salon <database_code>")


def parse_entities(value: str | None) -> set[str]:
    if not value:
        return set(ALL_ENTITIES)
    items = {x.strip().lower() for x in value.split(",") if x.strip()}
    invalid = items - set(ALL_ENTITIES)
    if invalid:
        raise SystemExit(f"Invalid entities: {invalid}. Allowed: {ALL_ENTITIES}")
    return items


async def link_services_to_profiles(factory, countries: set[str]) -> None:
    """Лінкує service.profile_id для нових/перейменованих services через
    canonical_key matching у service_profile_option.canonical_keys[].

    Запускається після sync, окремою транзакцією на країну.
    Існуючі заповнені profile_id НЕ перетираються — стабільно при перейменуваннях CRM.
    """
    from sqlalchemy import text as sql_text
    for country in sorted(countries):
        async with country_session(factory, country) as session:
            # Via profile.canonical_keys[]
            r1 = await session.execute(sql_text(f"""
                UPDATE {country}.service s
                SET profile_id = sub.profile_id
                FROM (
                  SELECT DISTINCT ON (canonical_key) canonical_key, id AS profile_id
                  FROM (
                    SELECT jsonb_array_elements_text(p.canonical_keys) AS canonical_key, p.id
                    FROM public.service_profile p
                    WHERE p.country = :c
                  ) x
                  ORDER BY canonical_key, profile_id
                ) sub
                WHERE s.canonical_key = sub.canonical_key
                  AND s.archive = false
                  AND s.profile_id IS NULL
            """), {"c": country})
            # Via primary canonical_key fallback
            r2 = await session.execute(sql_text(f"""
                UPDATE {country}.service s
                SET profile_id = p.id
                FROM public.service_profile p
                WHERE s.canonical_key = p.canonical_key
                  AND p.country = :c
                  AND s.archive = false
                  AND s.profile_id IS NULL
            """), {"c": country})
            await session.flush()
            print(f"[link:{country}] linked via canonical_keys={r1.rowcount} primary={r2.rowcount}")


async def embed_missing_after_sync(factory, countries: set[str]) -> None:
    """Догенерувати name_embedding для всіх послуг, у яких він NULL.

    Запускається після sync, окремою транзакцією на країну. Якщо OpenAI
    падає (квота, мережа) — лише логується; синк уже закомічений, наступний
    запуск догенерує те, що залишилось.
    """
    from sqlalchemy import update

    embedder = OpenAIEmbedder()
    BATCH = 100
    total = 0
    for country in sorted(countries):
        async with country_session(factory, country) as session:
            rows = (
                await session.execute(
                    select(Service.id, Service.name).where(
                        Service.archive.is_(False),
                        Service.name_embedding.is_(None),
                    )
                )
            ).all()
            if not rows:
                print(f"[embed:{country}] nothing to embed")
                continue
            print(f"[embed:{country}] services to embed: {len(rows)}")
            done = 0
            for i in range(0, len(rows), BATCH):
                chunk = rows[i : i + BATCH]
                try:
                    vectors = await embedder.embed_batch(
                        [r.name for r in chunk], normalize_names=True
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[embed:{country}] batch {i} failed: {exc}")
                    continue
                for r, vec in zip(chunk, vectors):
                    await session.execute(
                        update(Service).where(Service.id == r.id).values(name_embedding=vec)
                    )
                await session.flush()
                done += len(chunk)
                print(f"[embed:{country}]   {done} / {len(rows)}")
            total += done
    print(f"[embed] embedded total: {total}")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true")
    g.add_argument("--country", choices=["ua", "pl", "gb"])
    g.add_argument("--salon", help="database_code")
    parser.add_argument("--entities", help=f"Comma list. Default = all: {','.join(ALL_ENTITIES)}")
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="Не запускати embed-прохід після синку (за замовчуванням запускається, якщо синкаються services).",
    )
    args = parser.parse_args()

    entities = parse_entities(args.entities)
    salons = list(select_salons(args))

    engine = build_engine()
    factory = build_session_factory(engine)
    touched_countries: set[str] = set()
    try:
        for salon in salons:
            try:
                await sync_salon(salon, entities, factory)
                touched_countries.add(salon.country)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✖ FAILED salon {salon.database_code}: {exc}")

        if "services" in entities and touched_countries:
            print("\n=== Linking services → profiles ===")
            try:
                await link_services_to_profiles(factory, touched_countries)
            except Exception as exc:  # noqa: BLE001
                print(f"[link] failed: {exc}")

        if "services" in entities and not args.skip_embed and touched_countries:
            print("\n=== Embedding pass ===")
            try:
                await embed_missing_after_sync(factory, touched_countries)
            except Exception as exc:  # noqa: BLE001
                print(f"[embed] failed: {exc}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
