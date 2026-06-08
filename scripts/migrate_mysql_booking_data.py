"""Copies booking_london_predict MySQL data into the Postgres `booking` schema.

Prerequisites:
  * scripts/migrate_to_booking_schema.py has been run (booking schema + tables exist).
  * MySQL credentials in booking_london_predict/.env (DB_HOST/USER/PASSWORD/NAME).
  * Postgres credentials in .env / DATABASE_URL.

Behaviour:
  * Reads every booking_london_predict table from MySQL.
  * Inserts into Postgres with ON CONFLICT DO NOTHING (idempotent — safe to re-run).
  * Tables migrated: payment_systems, admin_users, locations, salons (extras merge),
    appointmentsleads, slot_reservations, translations, service_update_queue,
    data_update_jobs, users_booking_app.

Run:
  python -m scripts.migrate_mysql_booking_data

Notes:
  * Booking salon UUID is generated locally (catalog by_database_code). MySQL salons
    are matched to Postgres booking.salons by `code` (1:1 with our catalog).
  * camelCase MySQL columns are renamed to snake_case in Postgres:
      actualState -> actual_state, paymentStatus -> payment_status,
      appointmentId -> appointment_id, usersBookingapp -> users_booking_app.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import mysql.connector
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Repo root + booking_london_predict/.env (for MySQL DB_* vars)
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "booking_london_predict" / ".env", override=False)


def _mysql_conn():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        use_pure=True,
    )


def _pg_conn():
    dsn = os.getenv("AI_CHAT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Set AI_CHAT_DATABASE_URL or DATABASE_URL")
    if dsn.startswith("postgresql+"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return psycopg2.connect(dsn)


def _maybe_json(v):
    """MySQL returns JSON columns as str (because of CHECK json_valid). Decode -> dict.
    Returns psycopg2.extras.Json or None."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8")
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return None
    return psycopg2.extras.Json(v)


def _bool(v) -> bool:
    return bool(int(v)) if v is not None else False


# ───────────────────── migrators ─────────────────────
def migrate_payment_systems(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("SELECT id, name, provider, config, status, created_at FROM payment_systems")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.payment_systems (id, name, provider, config, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["name"], r["provider"], _maybe_json(r["config"]),
              r["status"] or "active", r["created_at"]))
    # Reset SERIAL to max(id)+1
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.payment_systems','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.payment_systems), 1))")
    print(f"[payment_systems] {len(rows)}")


def migrate_admin_users(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("SELECT id, email, password_hash, role, created_by, is_active, created_at "
               "FROM admin_users")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.admin_users (id, email, password_hash, role, created_by, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["email"], r["password_hash"], r["role"] or "admin",
              r["created_by"], _bool(r["is_active"]), r["created_at"]))
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.admin_users','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.admin_users), 1))")
    print(f"[admin_users] {len(rows)}")


def migrate_locations(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("SELECT slug, name, country, country_code, image, languages, status, sort_order, "
               "created_at FROM locations")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.locations
                (slug, name, country, country_code, image, languages, status, sort_order, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO NOTHING
        """, (r["slug"], r["name"], r["country"], r["country_code"] or "",
              r["image"], _maybe_json(r["languages"]), r["status"] or "planned",
              r["sort_order"] or 0, r["created_at"]))
    print(f"[locations] {len(rows)}")


def migrate_salons_extras(my, pg) -> None:
    """MySQL salons rows enrich booking.salons (booking.salons rows must already exist).

    Match by `code`. Adds presentation/payment fields that AI Chat doesn't have.
    """
    mc = my.cursor(dictionary=True)
    mc.execute("SELECT * FROM salons WHERE database_code IS NOT NULL")
    rows = mc.fetchall()
    pc = pg.cursor()
    updated = 0
    for r in rows:
        pc.execute("""
            UPDATE booking.salons SET
                address_line = COALESCE(%(address_line)s, address_line),
                postal_code = COALESCE(%(postal_code)s, postal_code),
                phone_display = COALESCE(%(phone_display)s, phone_display),
                phone_link = COALESCE(%(phone_link)s, phone_link),
                email = COALESCE(%(email)s, email),
                payment_location_id = COALESCE(%(payment_location_id)s, payment_location_id),
                image = COALESCE(%(image)s, image),
                area_icon = COALESCE(%(area_icon)s, area_icon),
                map_embed_url = COALESCE(%(map_embed_url)s, map_embed_url),
                map_link = COALESCE(%(map_link)s, map_link),
                telegram_token = COALESCE(%(telegram_token)s, telegram_token),
                telegram_chat_id = COALESCE(%(telegram_chat_id)s, telegram_chat_id),
                languages = COALESCE(%(languages)s, languages),
                menu_links = COALESCE(%(menu_links)s, menu_links),
                payment_enabled = %(payment_enabled)s,
                payment_system_id = %(payment_system_id)s
            WHERE code = %(code)s
        """, {
            "code": r["code"],
            "address_line": r["address_line"],
            "postal_code": r["postal_code"],
            "phone_display": r["phone_display"],
            "phone_link": r["phone_link"],
            "email": r["email"],
            "payment_location_id": r["payment_location_id"],
            "image": r["image"],
            "area_icon": r["area_icon"],
            "map_embed_url": r["map_embed_url"],
            "map_link": r["map_link"],
            "telegram_token": r["telegram_token"],
            "telegram_chat_id": r["telegram_chat_id"],
            "languages": _maybe_json(r["languages"]),
            "menu_links": _maybe_json(r["menu_links"]),
            "payment_enabled": _bool(r["payment_enabled"]),
            "payment_system_id": r["payment_system_id"],
        })
        if pc.rowcount:
            updated += 1
    print(f"[salons extras] updated {updated}/{len(rows)} (rest had no matching code in booking.salons)")


def migrate_appointmentsleads(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("""SELECT reference, client_info, appointments, actualState, paymentStatus,
                  appointmentId, created_at, servcategory, retry_count, next_retry_at,
                  last_error, salon_code, crm_appointment_ids
                  FROM appointmentsleads""")
    pc = pg.cursor()
    count = 0
    while True:
        chunk = mc.fetchmany(500)
        if not chunk:
            break
        psycopg2.extras.execute_batch(pc, """
            INSERT INTO booking.appointmentsleads
                (reference, client_info, appointments, actual_state, payment_status,
                 appointment_id, created_at, servcategory, retry_count, next_retry_at,
                 last_error, salon_code, crm_appointment_ids)
            VALUES (%(reference)s, %(client_info)s, %(appointments)s, %(actual_state)s,
                    %(payment_status)s, %(appointment_id)s, %(created_at)s,
                    %(servcategory)s, %(retry_count)s, %(next_retry_at)s,
                    %(last_error)s, %(salon_code)s, %(crm_appointment_ids)s)
            ON CONFLICT (reference) DO NOTHING
        """, [{
            "reference": r["reference"],
            "client_info": _maybe_json(r["client_info"]),
            "appointments": _maybe_json(r["appointments"]),
            "actual_state": r["actualState"],
            "payment_status": r["paymentStatus"],
            "appointment_id": r["appointmentId"],
            "created_at": r["created_at"],
            "servcategory": r["servcategory"],
            "retry_count": r["retry_count"] or 0,
            "next_retry_at": r["next_retry_at"],
            "last_error": r["last_error"],
            "salon_code": r["salon_code"],
            "crm_appointment_ids": _maybe_json(r["crm_appointment_ids"]),
        } for r in chunk], page_size=500)
        count += len(chunk)
        print(f"[appointmentsleads] {count}")
    print(f"[appointmentsleads] total {count}")


def migrate_slot_reservations(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("""SELECT id, employee_id, reservation_date, reservation_time,
                  visit_id, expires_at, created_at FROM slot_reservations""")
    pc = pg.cursor()
    count = 0
    while True:
        chunk = mc.fetchmany(500)
        if not chunk:
            break
        psycopg2.extras.execute_batch(pc, """
            INSERT INTO booking.slot_reservations
                (id, employee_id, reservation_date, reservation_time, visit_id, expires_at, created_at)
            VALUES (%(id)s, %(employee_id)s, %(reservation_date)s, %(reservation_time)s,
                    %(visit_id)s, %(expires_at)s, %(created_at)s)
            ON CONFLICT (id) DO NOTHING
        """, chunk, page_size=500)
        count += len(chunk)
    if count:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.slot_reservations','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.slot_reservations), 1))")
    print(f"[slot_reservations] {count}")


def migrate_translations(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("""SELECT id, salon_code, entity_type, entity_id, lang,
                  translated_name, translated_description, created_at, updated_at
                  FROM translations""")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.translations
                (id, salon_code, entity_type, entity_id, lang, translated_name,
                 translated_description, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["salon_code"], r["entity_type"], r["entity_id"], r["lang"],
              r["translated_name"], r["translated_description"],
              r["created_at"], r["updated_at"]))
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.translations','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.translations), 1))")
    print(f"[translations] {len(rows)}")


def migrate_service_update_queue(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("""SELECT id, salon_code, service_id, field_updates, status, retry_count,
                  max_retries, last_error, created_at, next_retry_at FROM service_update_queue""")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.service_update_queue
                (id, salon_code, service_id, field_updates, status, retry_count,
                 max_retries, last_error, created_at, next_retry_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["salon_code"], r["service_id"], _maybe_json(r["field_updates"]),
              r["status"] or "pending", r["retry_count"] or 0,
              r["max_retries"] or 10, r["last_error"], r["created_at"], r["next_retry_at"]))
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.service_update_queue','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.service_update_queue), 1))")
    print(f"[service_update_queue] {len(rows)}")


def migrate_data_update_jobs(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("""SELECT id, script_name, status, triggered_by, started_at, finished_at,
                  duration_ms, error_text, output_text FROM data_update_jobs""")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.data_update_jobs
                (id, script_name, status, triggered_by, started_at, finished_at,
                 duration_ms, error_text, output_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["script_name"], r["status"], r["triggered_by"],
              r["started_at"], r["finished_at"], r["duration_ms"],
              r["error_text"], r["output_text"]))
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.data_update_jobs','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.data_update_jobs), 1))")
    print(f"[data_update_jobs] {len(rows)}")


def migrate_users_booking_app(my, pg) -> None:
    mc = my.cursor(dictionary=True)
    mc.execute("SELECT id, username, password, role FROM usersBookingapp")
    rows = mc.fetchall()
    pc = pg.cursor()
    for r in rows:
        pc.execute("""
            INSERT INTO booking.users_booking_app (id, username, password, role)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (r["id"], r["username"], r["password"], r["role"] or "user"))
    if rows:
        pc.execute("SELECT setval(pg_get_serial_sequence('booking.users_booking_app','id'), "
                   "GREATEST((SELECT COALESCE(MAX(id),0) FROM booking.users_booking_app), 1))")
    print(f"[users_booking_app] {len(rows)}")


def main() -> None:
    my = _mysql_conn()
    pg = _pg_conn()
    try:
        # Order matters for FKs (payment_systems before salons extras, admin_users self-ref).
        migrate_payment_systems(my, pg)
        migrate_locations(my, pg)
        migrate_admin_users(my, pg)
        migrate_salons_extras(my, pg)
        migrate_translations(my, pg)
        migrate_service_update_queue(my, pg)
        migrate_data_update_jobs(my, pg)
        migrate_users_booking_app(my, pg)
        migrate_slot_reservations(my, pg)
        migrate_appointmentsleads(my, pg)
        pg.commit()
        print("\nAll MySQL booking data migrated into Postgres `booking` schema.")
    except Exception:
        pg.rollback()
        raise
    finally:
        my.close()
        pg.close()


if __name__ == "__main__":
    main()
