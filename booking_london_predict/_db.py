"""Postgres connection helper for booking_london_predict.

Replaces `mysql.connector.connect(...)` usage with psycopg2 against the AI Chat
Postgres `booking` schema. Drop-in:

    from _db import get_db
    conn = get_db()
    cur = conn.cursor(dict_rows=True)
    cur.execute("SELECT * FROM salons WHERE code = %s", (code,))
    rows = cur.fetchall()
    cur.close(); conn.close()

The wrapper sets `search_path = booking, public` on each connection so the
booking code can use unqualified table names (admin_users, salons, ...).
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import psycopg2.errors
import psycopg2.extras
from dotenv import load_dotenv

# Load .env from booking_london_predict/.env (and repo-root .env if present).
_BASE = Path(__file__).resolve().parent
load_dotenv(_BASE / ".env")
load_dotenv(_BASE.parent / ".env", override=False)

# Re-export so legacy `except mysql.connector.IntegrityError` callers can switch
# to `except _db.IntegrityError` without importing psycopg2 directly.
IntegrityError = psycopg2.IntegrityError
DataError = psycopg2.DataError
OperationalError = psycopg2.OperationalError


def _dsn() -> str:
    dsn = os.getenv("AI_CHAT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "AI_CHAT_DATABASE_URL (or DATABASE_URL) is not set. "
            "Add it to booking_london_predict/.env."
        )
    if dsn.startswith("postgresql+"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


class _BookingConnection:
    """Thin psycopg2 wrapper that mimics mysql.connector.connect() API.

    Why a wrapper:
      * default cursor() returns a tuple cursor; we want dict_rows=True keyword
        to mirror mysql.connector's `cursor(dictionary=True)`.
      * sets search_path to `booking, public` on every connection.
    """

    def __init__(self) -> None:
        self._conn = psycopg2.connect(_dsn())
        with self._conn.cursor() as cur:
            cur.execute("SET search_path TO booking, public")
        self._conn.commit()

    def cursor(self, dict_rows: bool = False, dictionary: bool = False):
        """Returns a cursor. Pass dict_rows=True (or dictionary=True for
        compat with mysql.connector callsites) to get RealDictCursor."""
        if dict_rows or dictionary:
            return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "_BookingConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db() -> _BookingConnection:
    """Returns a new connection to the Postgres `booking` schema."""
    return _BookingConnection()


def sqlalchemy_url() -> str:
    """Return a SQLAlchemy-friendly URL for code that uses `create_engine`.

    Adds the psycopg2 driver suffix so SQLAlchemy uses the sync driver.
    """
    dsn = _dsn()
    # SQLAlchemy needs an explicit driver. psycopg2 is default for postgresql://
    # but we need to also pin schema search path. We do that via connect args
    # in the caller (engine.connect().execute("SET search_path ...")) OR via
    # the options query param.
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options=-csearch_path%3Dbooking%2Cpublic"
