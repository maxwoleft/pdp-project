"""Сервіс синку записів з нашої БД у CRM (api.aihelps.com).

Викликається з create_booking / cancel_booking tools. Якщо CRM_PUSH_ENABLED=False —
сервіс просто логуватиме виклик і повертатиме (None, "disabled"), нічого не пушачи.

Маппінг ID:
  - Усі наші ID збережені як f"{salon_id}:{crm_id}". Перед викликом CRM
    розгортаємо їх назад до чистого crm_id.
  - Booking.crm_id зберігаємо після успішного push, щоб скасування знало id у CRM.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.config import get_settings
from app.infrastructure.db.models.scheduling import Booking
from app.integrations.crm.client import CRMClient
from app.integrations.crm.salons_catalog import by_salon_id

log = logging.getLogger(__name__)


def strip_ns(prefixed_id: str) -> str:
    """f'{salon_id}:{crm_id}' -> '{crm_id}'."""
    return prefixed_id.split(":", 1)[1] if ":" in prefixed_id else prefixed_id


def to_crm_iso(dt: datetime) -> str:
    """Формат, який очікує CRM: '2026-04-15T14:00:00.000Z'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class CRMBookingSync:
    """Push наших booking у CRM. Поведінка керується feature flag."""

    def __init__(self) -> None:
        self._enabled = get_settings().crm_push_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def push_booking(
        self,
        booking: Booking,
        *,
        client_phone: str,
        client_name: str,
        client_email: str | None = None,
    ) -> tuple[str | None, str]:
        """Створює запис у CRM. Повертає (crm_appointment_id, status_msg).

        status_msg: "ok" | "disabled" | "error: ..."
        """
        if not self._enabled:
            log.info("CRM push DISABLED — booking %s only saved locally", booking.id)
            return None, "disabled"

        salon_cfg = by_salon_id(booking.salon_id)
        if not salon_cfg:
            return None, "error: salon not in catalog"

        crm = CRMClient(salon_cfg.database_code)
        try:
            await crm.authenticate()

            location = (
                salon_cfg.location_sales
                or await crm.get_primary_location_id()
            )
            if not location:
                return None, "error: no location for salon"

            # 1) Знайти або створити клієнта в CRM
            existing = await crm.find_client_by_phone(client_phone)
            if existing and existing.get("id"):
                client_crm_id = existing["id"]
            else:
                created = await crm.create_client(
                    name=client_name,
                    phone=client_phone,
                    location=location,
                    email=client_email,
                )
                client_crm_id = created.get("id")
                if not client_crm_id:
                    return None, "error: client creation returned no id"

            # 2) Створити appointment
            result = await crm.create_appointment(
                client_id=client_crm_id,
                professional_id=strip_ns(booking.employee_id),
                service_id=strip_ns(booking.service_id),
                location=location,
                start_iso=to_crm_iso(booking.start_at),
            )
            crm_appointment_id = result.get("id") if isinstance(result, dict) else None
            return crm_appointment_id, "ok"
        except Exception as exc:  # noqa: BLE001
            log.exception("CRM push failed for booking %s", booking.id)
            return None, f"error: {exc}"
        finally:
            await crm.close()

    async def cancel_in_crm(self, crm_appointment_id: str | None, salon_id: str) -> str:
        if not self._enabled:
            return "disabled"
        if not crm_appointment_id:
            return "skipped: no crm_id"

        salon_cfg = by_salon_id(salon_id)
        if not salon_cfg:
            return "error: salon not in catalog"

        crm = CRMClient(salon_cfg.database_code)
        try:
            await crm.authenticate()
            await crm.cancel_appointment(crm_appointment_id)
            return "ok"
        except Exception as exc:  # noqa: BLE001
            log.exception("CRM cancel failed for appointment %s", crm_appointment_id)
            return f"error: {exc}"
        finally:
            await crm.close()
