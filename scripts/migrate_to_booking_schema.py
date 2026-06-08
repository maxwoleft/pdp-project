"""Consolidates AI Chat salon + all booking_london_predict tables into a single
`booking` schema in the AI Chat Postgres database.

What it does (idempotent — safe to re-run):
  1. CREATE SCHEMA booking
  2. CREATE TABLE booking.* for all booking_london_predict tables
  3. Copy ua/pl/gb.salon rows -> booking.salons (with country column)
  4. Re-point FK constraints in ua/pl/gb.{category, employee, position,
     service, client, product, product_category, product_order, booking}
     from country.salon -> booking.salons
  5. DROP TABLE ua/pl/gb.salon

Run: python -m scripts.migrate_to_booking_schema

WARNING: data-altering. Take pg_dump first on prod. Locally we restore by
re-running scripts.init_db + scripts.sync_from_crm.

This script does NOT touch MySQL. Data move from MySQL pdpbooking ->
booking schema lives in scripts/migrate_mysql_booking_data.py.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.infrastructure.db.session import build_engine
from app.integrations.crm.salons_catalog import SALONS, by_database_code

COUNTRIES = ("ua", "pl", "gb")

# Tables in country schemas that hold a FK to the local salon table.
# Format: (table_name, fk_constraint_name). FK column is always `salon_id`.
COUNTRY_TABLES_WITH_SALON_FK = [
    ("category", "fk_category_salon_id_salon"),
    ("position", "fk_position_salon_id_salon"),
    ("service", "fk_service_salon_id_salon"),
    ("employee", "fk_employee_salon_id_salon"),
    ("client", "fk_client_salon_id_salon"),
    ("product", "fk_product_salon_id_salon"),
    ("product_category", "fk_product_category_salon_id_salon"),
    ("product_order", "fk_product_order_salon_id_salon"),
    ("booking", "fk_booking_salon_id_salon"),  # AI Chat's booking table (different from schema)
]


DDL_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS booking;
"""

DDL_PAYMENT_SYSTEMS = """
CREATE TABLE IF NOT EXISTS booking.payment_systems (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    config JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_SALONS = """
CREATE TABLE IF NOT EXISTS booking.salons (
    id VARCHAR(80) PRIMARY KEY,
    code VARCHAR(20) UNIQUE NOT NULL,
    country VARCHAR(2) NOT NULL CHECK (country IN ('ua','pl','gb')),
    name VARCHAR(255) NOT NULL,
    city VARCHAR(100) NOT NULL,
    location_slug VARCHAR(50) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'planned'
        CHECK (status IN ('active','planned','inactive')),
    timezone VARCHAR(50) NOT NULL,
    archive BOOLEAN NOT NULL DEFAULT FALSE,
    database_code VARCHAR(20) UNIQUE,
    data_dir VARCHAR(255),
    working_hours JSONB,
    address_line VARCHAR(500),
    postal_code VARCHAR(50),
    phone_display VARCHAR(50),
    phone_link VARCHAR(50),
    email VARCHAR(100),
    payment_location_id VARCHAR(50),
    image VARCHAR(255),
    area_icon VARCHAR(255),
    map_embed_url TEXT,
    map_link TEXT,
    telegram_token VARCHAR(255),
    telegram_chat_id VARCHAR(100),
    languages JSONB,
    menu_links JSONB,
    payment_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    payment_system_id INT REFERENCES booking.payment_systems(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_booking_salons_country ON booking.salons (country);
CREATE INDEX IF NOT EXISTS ix_booking_salons_location_slug ON booking.salons (location_slug);
CREATE INDEX IF NOT EXISTS ix_booking_salons_status ON booking.salons (status);
"""

DDL_LOCATIONS = """
CREATE TABLE IF NOT EXISTS booking.locations (
    slug VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    country VARCHAR(50) NOT NULL,
    country_code VARCHAR(5) NOT NULL DEFAULT '',
    image VARCHAR(255),
    languages JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'planned'
        CHECK (status IN ('active','planned','inactive')),
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_ADMIN_USERS = """
CREATE TABLE IF NOT EXISTS booking.admin_users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'admin'
        CHECK (role IN ('superadmin','admin')),
    created_by INT REFERENCES booking.admin_users(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_APPTLEADS = """
CREATE TABLE IF NOT EXISTS booking.appointmentsleads (
    reference VARCHAR(36) PRIMARY KEY,
    client_info JSONB NOT NULL,
    appointments JSONB NOT NULL,
    actual_state VARCHAR(255),
    payment_status VARCHAR(255),
    appointment_id VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    servcategory TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    salon_code VARCHAR(20),
    crm_appointment_ids JSONB
);
CREATE INDEX IF NOT EXISTS ix_apptleads_retry_queue ON booking.appointmentsleads (next_retry_at);
CREATE INDEX IF NOT EXISTS ix_apptleads_salon_code ON booking.appointmentsleads (salon_code);
"""

DDL_SLOT_RESERVATIONS = """
CREATE TABLE IF NOT EXISTS booking.slot_reservations (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(255) NOT NULL,
    reservation_date VARCHAR(20) NOT NULL,
    reservation_time VARCHAR(10) NOT NULL,
    visit_id VARCHAR(255),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, reservation_date, reservation_time)
);
CREATE INDEX IF NOT EXISTS ix_slot_reservations_expires_at ON booking.slot_reservations (expires_at);
"""

DDL_TRANSLATIONS = """
CREATE TABLE IF NOT EXISTS booking.translations (
    id SERIAL PRIMARY KEY,
    salon_code VARCHAR(20) NOT NULL,
    entity_type VARCHAR(20) NOT NULL CHECK (entity_type IN ('service','category')),
    entity_id VARCHAR(64) NOT NULL,
    lang VARCHAR(5) NOT NULL,
    translated_name TEXT NOT NULL,
    translated_description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (salon_code, entity_type, entity_id, lang)
);
"""

DDL_SERVICE_UPDATE_QUEUE = """
CREATE TABLE IF NOT EXISTS booking.service_update_queue (
    id SERIAL PRIMARY KEY,
    salon_code VARCHAR(20) NOT NULL,
    service_id VARCHAR(64) NOT NULL,
    field_updates JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','completed','failed')),
    retry_count INT NOT NULL DEFAULT 0,
    max_retries INT NOT NULL DEFAULT 10,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_retry_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_service_update_queue_status ON booking.service_update_queue (status, next_retry_at);
"""

DDL_DATA_UPDATE_JOBS = """
CREATE TABLE IF NOT EXISTS booking.data_update_jobs (
    id SERIAL PRIMARY KEY,
    script_name VARCHAR(20) NOT NULL
        CHECK (script_name IN ('services','categories','employees')),
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('running','success','error','busy','interrupted')),
    triggered_by INT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INT,
    error_text TEXT,
    output_text TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_script_started ON booking.data_update_jobs (script_name, started_at);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON booking.data_update_jobs (status);
"""

DDL_USERS_BOOKING_APP = """
CREATE TABLE IF NOT EXISTS booking.users_booking_app (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'user'
);
"""

ALL_DDL = [
    DDL_SCHEMA,
    DDL_PAYMENT_SYSTEMS,
    DDL_SALONS,
    DDL_LOCATIONS,
    DDL_ADMIN_USERS,
    DDL_APPTLEADS,
    DDL_SLOT_RESERVATIONS,
    DDL_TRANSLATIONS,
    DDL_SERVICE_UPDATE_QUEUE,
    DDL_DATA_UPDATE_JOBS,
    DDL_USERS_BOOKING_APP,
]


def _split_statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


async def create_schema_and_tables(conn) -> None:
    for ddl in ALL_DDL:
        for stmt in _split_statements(ddl):
            await conn.execute(text(stmt))
    print("[ddl] schema booking + all tables ready")


async def copy_salons_from_country_schemas(conn) -> int:
    """Copy ua/pl/gb.salon rows into booking.salons.

    The country column is derived from catalog (by database_code).
    If a salon is already in booking.salons (by id), we leave it (idempotent).
    """
    inserted = 0
    for country in COUNTRIES:
        # Pull rows from country schema if salon table still exists.
        check = await conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = 'salon'"
        ), {"s": country})
        if not check.fetchone():
            print(f"[migrate-salons] {country}.salon already dropped, skip copy")
            continue

        rows = (await conn.execute(text(
            f'SELECT id, name, city, address, phone, timezone, working_hours, '
            f'archive, code, database_code, data_dir, status, location_slug, sort_order '
            f'FROM "{country}".salon'
        ))).mappings().all()

        for r in rows:
            cfg = by_database_code(r["database_code"]) if r["database_code"] else None
            country_code = cfg.country if cfg else country
            await conn.execute(text("""
                INSERT INTO booking.salons (
                    id, code, country, name, city, location_slug, sort_order, status,
                    timezone, archive, database_code, data_dir,
                    working_hours, address_line, phone_display
                ) VALUES (
                    :id, :code, :country, :name, :city, :location_slug, :sort_order, :status,
                    :timezone, :archive, :database_code, :data_dir,
                    :working_hours, :address_line, :phone_display
                )
                ON CONFLICT (id) DO NOTHING
            """), {
                "id": r["id"],
                "code": r["code"] or r["id"][:20],  # fallback if code missing
                "country": country_code,
                "name": r["name"],
                "city": r["city"],
                "location_slug": r["location_slug"] or country_code,
                "sort_order": r["sort_order"] or 0,
                "status": r["status"] or "planned",
                "timezone": r["timezone"],
                "archive": r["archive"],
                "database_code": r["database_code"],
                "data_dir": r["data_dir"],
                "working_hours": r["working_hours"],
                "address_line": r["address"],
                "phone_display": r["phone"],
            })
            inserted += 1
    print(f"[migrate-salons] inserted/skipped {inserted} salons into booking.salons")
    return inserted


async def repoint_fks(conn) -> None:
    """ALTER FK constraints in country schemas: salon -> booking.salons."""
    for country in COUNTRIES:
        for tbl, fk_name in COUNTRY_TABLES_WITH_SALON_FK:
            # Skip if country schema table doesn't exist (e.g. AI Chat optional tables)
            check = await conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ), {"s": country, "t": tbl})
            if not check.fetchone():
                continue

            new_fk_name = f"{fk_name}_to_booking"
            # Skip if already re-pointed
            check2 = await conn.execute(text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = :s AND table_name = :t AND constraint_name = :c"
            ), {"s": country, "t": tbl, "c": new_fk_name})
            if check2.fetchone():
                continue

            # Drop old FK if it exists
            try:
                await conn.execute(text(
                    f'ALTER TABLE "{country}".{tbl} DROP CONSTRAINT IF EXISTS {fk_name}'
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠ {country}.{tbl} drop {fk_name}: {exc}")

            # Add new FK -> booking.salons
            await conn.execute(text(f"""
                ALTER TABLE "{country}".{tbl}
                ADD CONSTRAINT {new_fk_name}
                FOREIGN KEY (salon_id) REFERENCES booking.salons(id) ON DELETE CASCADE
            """))
            print(f"  ✓ {country}.{tbl}: FK -> booking.salons")
    print("[fk-repoint] done")


async def drop_country_salon_tables(conn) -> None:
    for country in COUNTRIES:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{country}".salon'))
        print(f"[drop] {country}.salon")


async def main() -> None:
    engine = build_engine()
    try:
        async with engine.begin() as conn:
            await create_schema_and_tables(conn)
            await copy_salons_from_country_schemas(conn)
            await repoint_fks(conn)
            await drop_country_salon_tables(conn)
        print("\nMigration to booking schema complete.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
