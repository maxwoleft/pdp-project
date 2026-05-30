"""Синхронізація клієнтів з CRM.

Окремий скрипт, бо клієнтів можуть бути тисячі. Підтримує два режими:

  --mode all              # тягнути ВСІХ клієнтів (мінімальні поля + per-client деталі)
  --mode search           # тільки тих, хто збігається з фільтрами

Запуск:
  python -m scripts.sync_clients --salon 776611 --mode all
  python -m scripts.sync_clients --salon 776611 --mode search --name Марія --phone 0638622062
  python -m scripts.sync_clients --country ua --mode all
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.db.models.scheduling import Client
from app.infrastructure.db.session import build_engine, build_session_factory, country_session
from app.integrations.crm.client import CRMClient
from app.integrations.crm.salons_catalog import (
    SALONS,
    SalonConfig,
    by_country,
    by_database_code,
)


def numbers_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).date()
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except (TypeError, ValueError):
        return None


def match_client(c: dict, names: list[str], phones: list[str], emails: list[str]) -> bool:
    name_val = (c.get("name") or "").strip().casefold()
    raw_phones = c.get("phone") or []
    if not isinstance(raw_phones, list):
        raw_phones = [raw_phones]
    raw_emails = c.get("email") or []
    if not isinstance(raw_emails, list):
        raw_emails = [raw_emails]

    client_phones = [numbers_only(str(p)) for p in raw_phones]
    client_emails = [str(e).strip().lower() for e in raw_emails]

    for n in names:
        if n.strip().casefold() in name_val:
            return True
    for q in phones:
        qd = numbers_only(q)
        if qd and any(qd in p for p in client_phones):
            return True
    for q in emails:
        qn = q.strip().lower()
        if qn and any(qn in e for e in client_emails):
            return True
    return False


async def upsert_client(session, salon_id: str, raw: dict) -> None:
    crm_id = raw.get("id")
    if not crm_id:
        return

    values = {
        "id": f"{salon_id}:{crm_id}",
        "salon_id": salon_id,
        "crm_id": crm_id,
        "name": raw.get("name"),
        "title": raw.get("title"),
        "gender": raw.get("gender"),
        "birthday": parse_date(raw.get("birthday")),
        "balance": raw.get("balance") or 0,
        "bonus": raw.get("bonus") or 0,
        "card_number": raw.get("card_number"),
        "phone": raw.get("phone"),
        "email": raw.get("email"),
        "categories": raw.get("categories"),
        "categories_names": raw.get("categories_names"),
        "first_visit": parse_dt(raw.get("first_visit")),
        "first_visit_description": raw.get("first_visit_description"),
        "last_visit": parse_dt(raw.get("last_visit")),
        "last_visit_description": raw.get("last_visit_description"),
        "history": raw.get("history"),
        "feedback": raw.get("feedback"),
        "additional_fields": raw.get("additional_fields"),
        "deposit_client": raw.get("deposit_client"),
        "referral_source": raw.get("referral_source"),
        "referral_source_name": raw.get("referral_source_name"),
        "status": raw.get("status"),
        "comments": raw.get("comments"),
        "archive": bool(raw.get("archive")),
    }

    stmt = insert(Client).values(**values)
    update_set = {k: stmt.excluded[k] for k in values if k not in ("id", "salon_id", "crm_id")}
    stmt = stmt.on_conflict_do_update(index_elements=[Client.id], set_=update_set)
    await session.execute(stmt)


async def sync_one_salon(
    salon: SalonConfig,
    factory,
    mode: str,
    names: list[str],
    phones: list[str],
    emails: list[str],
    fetch_history: bool,
) -> None:
    print(f"\n=== {salon.country.upper()} | {salon.name} ({salon.database_code}) — mode={mode} ===")
    crm = CRMClient(salon.database_code)
    try:
        await crm.authenticate()
        minimal = await crm.get_clients_minimal()
        print(f"  fetched minimal: {len(minimal)}")

        if mode == "search":
            target_ids = [c["id"] for c in minimal if match_client(c, names, phones, emails)]
        else:
            target_ids = [c["id"] for c in minimal if c.get("id")]
        print(f"  to import: {len(target_ids)}")

        async with country_session(factory, salon.country) as session:
            for idx, cid in enumerate(target_ids, start=1):
                try:
                    details = await crm.get_client_details(cid)
                    if fetch_history:
                        details["history"] = await crm.get_client_history(cid)
                    details["id"] = cid
                    await upsert_client(session, salon.salon_id, details)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠ client {cid}: {exc}")
                if idx % 50 == 0:
                    print(f"  ... {idx}/{len(target_ids)}")
        print(f"  ✓ done: {len(target_ids)}")
    finally:
        await crm.close()


def select_salons(args) -> list[SalonConfig]:
    if args.salon:
        s = by_database_code(args.salon)
        if not s:
            raise SystemExit(f"Salon {args.salon} not found")
        return [s]
    if args.country:
        return by_country(args.country)
    if args.all:
        return SALONS
    raise SystemExit("Specify --all, --country, or --salon")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true")
    g.add_argument("--country", choices=["ua", "pl", "gb"])
    g.add_argument("--salon", help="database_code")
    parser.add_argument("--mode", choices=["all", "search"], default="search")
    parser.add_argument("--name", action="append", default=[])
    parser.add_argument("--phone", action="append", default=[])
    parser.add_argument("--email", action="append", default=[])
    parser.add_argument("--no-history", action="store_true", help="Не тягнути історію візитів")
    args = parser.parse_args()

    if args.mode == "search" and not (args.name or args.phone or args.email):
        raise SystemExit("--mode search requires at least one of --name/--phone/--email")

    salons = select_salons(args)
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        for salon in salons:
            try:
                await sync_one_salon(
                    salon,
                    factory,
                    mode=args.mode,
                    names=args.name,
                    phones=args.phone,
                    emails=args.email,
                    fetch_history=not args.no_history,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  ✖ FAILED salon {salon.database_code}: {exc}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
