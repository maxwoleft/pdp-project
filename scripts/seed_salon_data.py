"""Імпорт даних конкретного салону з JSON-файлів.

Очікує структуру:
    data/<country>/<salon_id>/categories.json
    data/<country>/<salon_id>/services.json
    data/<country>/<salon_id>/employees.json
    data/<country>/<salon_id>/freetime.json
    data/<country>/<salon_id>/products.json   (опційно)

ID-збереження: щоб уникнути колізій між салонами (зовнішні UUID можуть повторюватись),
namespace-имо їх як f"{salon_id}:{external_id}". FK всередині салону так само.

Запуск:
    python -m scripts.seed_salon_data --country ua --salon <salon_id> --dir data/ua/<salon_id>

Для прикладу можна імпортувати examples/ як демо-салон UK:
    python -m scripts.seed_salon_data --country gb --salon <some_uk_salon_id> --dir examples
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from app.infrastructure.db.models.catalog import (
    Category,
    Position,
    Product,
    ProductCategory,
    Service,
)
from app.infrastructure.db.models.scheduling import TimeSlot
from app.infrastructure.db.models.staff import Employee, EmployeePosition
from app.infrastructure.db.session import build_engine, build_session_factory, country_session


def ns(salon_id: str, ext_id: str) -> str:
    """Namespace external ID by salon to avoid cross-salon collisions."""
    return f"{salon_id}:{ext_id}"


async def import_salon(country: str, salon_id: str, data_dir: Path) -> None:
    engine = build_engine()
    factory = build_session_factory(engine)

    async with country_session(factory, country) as session:
        # 1. Categories
        cats_raw = json.loads((data_dir / "categories.json").read_text(encoding="utf-8"))
        for c in cats_raw:
            session.add(Category(
                id=ns(salon_id, c["id"]),
                salon_id=salon_id,
                name=c["name"],
                parent_id=ns(salon_id, c["parent"]) if c.get("parent") else None,
                picture=c.get("picture"),
                archive=c.get("archive", False),
            ))
        await session.flush()
        print(f"  categories: {len(cats_raw)}")

        # 2. Positions + Services (positions виводимо з services + employees)
        services_raw = json.loads((data_dir / "services.json").read_text(encoding="utf-8"))
        employees_raw = json.loads((data_dir / "employees.json").read_text(encoding="utf-8"))

        position_ids: dict[str, str] = {}  # ext_id -> ns_id
        position_names: dict[str, str] = {}
        for emp in employees_raw:
            for pid, pname in zip(emp.get("positions", []), emp.get("position_names", [])):
                position_ids[pid] = ns(salon_id, pid)
                position_names[pid] = pname
        for s in services_raw:
            pid = s.get("position")
            if pid and pid not in position_ids:
                position_ids[pid] = ns(salon_id, pid)
                position_names[pid] = position_names.get(pid, "Unknown")

        for ext_pid, name in position_names.items():
            session.add(Position(
                id=position_ids[ext_pid],
                salon_id=salon_id,
                name=name,
            ))
        await session.flush()
        print(f"  positions: {len(position_ids)}")

        # Services (deduplicate by id)
        seen_services: set[str] = set()
        for s in services_raw:
            if s["id"] in seen_services:
                continue
            seen_services.add(s["id"])
            session.add(Service(
                id=ns(salon_id, s["id"]),
                salon_id=salon_id,
                name=s["name"],
                description=s.get("description"),
                description_plain=s.get("descriptionPlaintext"),
                duration_min=int(s["duration"]),
                price=s["price"],
                price_currency=s["price_currency"],
                gender=s.get("gender", "both"),
                category_id=ns(salon_id, s["category"]),
                position_id=ns(salon_id, s["position"]),
                archive=s.get("archive", False),
            ))
        await session.flush()
        print(f"  services: {len(seen_services)}")

        # 3. Employees + EmployeePosition
        seen_emp: set[str] = set()
        for e in employees_raw:
            if e["id"] in seen_emp:
                continue
            seen_emp.add(e["id"])
            emp_id = ns(salon_id, e["id"])
            session.add(Employee(
                id=emp_id,
                salon_id=salon_id,
                name=e["name"],
                title=e.get("title"),
                phones=e.get("phone"),
                emails=e.get("email"),
                roles=e.get("roles"),
                comments=e.get("comments"),
                prepayment_required=e.get("prepaymentRequired", False),
                archive=e.get("archive", False),
            ))
            for ext_pid in e.get("positions", []):
                session.add(EmployeePosition(
                    employee_id=emp_id,
                    position_id=ns(salon_id, ext_pid),
                ))
        await session.flush()
        print(f"  employees: {len(seen_emp)}")

        # 4. Free time → TimeSlot
        ft_path = data_dir / "freetime.json"
        if ft_path.exists():
            ft_raw = json.loads(ft_path.read_text(encoding="utf-8"))
            slot_count = 0
            for ext_emp_id, days in ft_raw.items():
                emp_id = ns(salon_id, ext_emp_id)
                for date_str, times in days.items():
                    for hhmm in times:
                        dt = datetime.fromisoformat(f"{date_str}T{hhmm}:00")
                        session.add(TimeSlot(
                            employee_id=emp_id,
                            slot_at=dt,
                            duration_min=15,
                            is_booked=False,
                        ))
                        slot_count += 1
            print(f"  time_slots: {slot_count}")

        # 5. Products (опційно)
        prod_path = data_dir / "products.json"
        if prod_path.exists():
            prods_raw = json.loads(prod_path.read_text(encoding="utf-8"))
            seen_pcat: set[str] = set()
            seen_prod: set[str] = set()
            for p in prods_raw:
                cat_id = p.get("category")
                if cat_id and cat_id not in seen_pcat:
                    seen_pcat.add(cat_id)
                    session.add(ProductCategory(
                        id=ns(salon_id, cat_id),
                        salon_id=salon_id,
                        name=p.get("category_name") or "Unknown",
                    ))
            await session.flush()
            for p in prods_raw:
                if p["id"] in seen_prod:
                    continue
                seen_prod.add(p["id"])
                session.add(Product(
                    id=ns(salon_id, p["id"]),
                    salon_id=salon_id,
                    name=p["name"],
                    description=p.get("description"),
                    barcode=p.get("barcode"),
                    vendor_code=p.get("vendor_code"),
                    units=p.get("units", "pcs"),
                    price=p["price"],
                    price_currency=p["supply_price_currency"],
                    category_id=ns(salon_id, p["category"]) if p.get("category") else None,
                    archive=p.get("archive", False),
                ))
            print(f"  products: {len(seen_prod)}")

    await engine.dispose()
    print(f"Done: salon {salon_id} in {country}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--salon", required=True, help="salon UUID (з seed_salons)")
    parser.add_argument("--dir", required=True, help="каталог з JSON-файлами")
    args = parser.parse_args()
    asyncio.run(import_salon(args.country, args.salon, Path(args.dir)))


if __name__ == "__main__":
    main()
