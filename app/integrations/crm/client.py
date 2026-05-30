"""CRM client (api.aihelps.com).

Один клас для всіх дзвінків у CRM:
- авторизація по database_code
- GET з ретраями
- готові методи для основних ендпоінтів

Використовуємо httpx (async) — щоб скрипти і фонові воркери жили в одному event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

API_BASE_URL = "https://api.aihelps.com/v1"
TOKEN_URL = f"{API_BASE_URL}/auth/database"

# Спільні дані додатку (з examples/config.py)
APPLICATION_ID = "a9188d6e-b1bb-46b1-b70f-c14debefd7d7"
APPLICATION_SECRET = "e60559fa-8791-471a-85ac-608b5ff9d873"
AUTH_LOCATION_ID = "a47cbc05-5ce6-4551-9456-28ccb52bbb11"
DEFAULT_FREETIME_LOCATION_ID = "a47cbc05-5ce6-4551-9456-28ccb52bbb11"

REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF = 60.0  # секунд між ретраями (як у прикладі)


class CRMError(RuntimeError):
    pass


class CRMClient:
    """CRM API клієнт для одного салону (одного database_code)."""

    def __init__(self, database_code: str) -> None:
        self.database_code = database_code
        self._token: str | None = None
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def __aenter__(self) -> "CRMClient":
        await self.authenticate()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    # ──────────────────────────────────────────────────────────────
    async def authenticate(self) -> str:
        params = {
            "application_id": APPLICATION_ID,
            "application_secret": APPLICATION_SECRET,
            "location": AUTH_LOCATION_ID,
            "database_code": self.database_code,
        }
        data = await self._request("GET", TOKEN_URL, params=params, auth=False)
        token = data.get("access_token")
        if not token:
            raise CRMError(f"No access_token returned for db={self.database_code}")
        self._token = token
        return token

    async def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_body: dict | None = None,
        auth: bool = True,
    ) -> Any:
        headers: dict[str, str] = {}
        if auth:
            if not self._token:
                await self.authenticate()
            headers["Authorization"] = f"Bearer {self._token}"

        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._http.request(
                    method, url, params=params, json=json_body, headers=headers
                )
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 401 and auth:
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._token}"
                    continue
                if attempt == MAX_RETRIES - 1:
                    break
                await asyncio.sleep(RETRY_BACKOFF)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt == MAX_RETRIES - 1:
                    break
                await asyncio.sleep(RETRY_BACKOFF)
        raise CRMError(f"{method} {url} failed after {MAX_RETRIES} attempts: {last_exc}")

    # ──────────────────────────────────────────────────────────────
    # Високорівневі методи
    # ──────────────────────────────────────────────────────────────
    async def get_categories(self) -> list[dict]:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/services/categories",
            params={"fields": "name,parent,picture,archive", "archive": "false"},
        )

    async def get_services(self) -> list[dict]:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/services",
            params={
                "fields": "name,description,duration,price_currency,category,gender,location_prices,archive",
            },
        )

    async def get_employees(self) -> list[dict]:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/employees",
            params={
                "fields": (
                    "name,title,phone,email,photo_exists,photo,positions,position_names,"
                    "roles,archive,prepaymentRequired,comments,commentsPlainText"
                )
            },
        )

    async def get_product_categories(self) -> list[dict]:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/products/categories",
            params={"fields": "name,archive"},
        )

    async def get_products(self) -> list[dict]:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/products",
            params={
                "fields": (
                    "name,description,category,vendor_code,barcode,volume,"
                    "supply_price,supply_price_currency,units,location_prices,"
                    "portion_quantity,stocks,tare_weight,critical_quantity,archive"
                )
            },
        )

    async def get_locations(self) -> list[dict]:
        """Список локацій салону. id повертається автоматично, archive — теж."""
        return await self._request(
            "GET", f"{API_BASE_URL}/locations", params={"fields": "name"}
        )

    async def get_primary_location_id(self) -> str | None:
        """Перший не-архівний location."""
        for loc in await self.get_locations() or []:
            if not loc.get("archive") and loc.get("id"):
                return loc["id"]
        return None

    async def get_all_location_ids(self) -> list[str]:
        """Усі не-архівні location.id салону. У деяких салонів їх кілька."""
        return [
            loc["id"]
            for loc in await self.get_locations() or []
            if loc.get("id") and not loc.get("archive")
        ]

    async def get_freetime(
        self,
        date_from: str,
        date_to: str,
        duration: str = "30",
        step: str = "15m",
        location: str = DEFAULT_FREETIME_LOCATION_ID,
    ) -> dict:
        return await self._request(
            "GET",
            f"{API_BASE_URL}/employees/free_time",
            params={
                "from": date_from,
                "to": date_to,
                "duration": duration,
                "step": step,
                "location": location,
            },
        )

    async def get_clients_minimal(self) -> list[dict]:
        data = await self._request(
            "GET",
            f"{API_BASE_URL}/clients",
            params={"fields": "archive,name,phone,email"},
        )
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return [c for c in (data or []) if not c.get("archive")]

    async def get_client_details(self, client_id: str) -> dict:
        data = await self._request(
            "GET",
            f"{API_BASE_URL}/clients/{client_id}",
            params={
                "fields": (
                    "name,title,gender,birthday,balance,bonus,card_number,phone,email,"
                    "categories,categories_names,first_visit,first_visit_description,"
                    "last_visit,last_visit_description,feedback,additional_fields,"
                    "deposit_client,referral_source,referral_source_name,status,archive,"
                    "professional,comments"
                )
            },
        )
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data or {}

    # ──────────────────────────────────────────────────────────────
    # Write methods (booking lifecycle)
    # ──────────────────────────────────────────────────────────────
    async def find_client_by_phone(self, phone: str) -> dict | None:
        """Шукає існуючого клієнта по телефону. Повертає перший збіг або None."""
        data = await self._request(
            "GET",
            f"{API_BASE_URL}/clients",
            params={"fields": "name,phone,archive", "phone": phone},
        )
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        for c in data or []:
            if not c.get("archive"):
                return c
        return None

    async def create_client(self, *, name: str, phone: str, location: str, email: str | None = None) -> dict:
        """Створює клієнта в CRM. id генерується автоматично."""
        body: dict[str, Any] = {
            "name": name,
            "phone": [phone],
            "location": location,
        }
        if email:
            body["email"] = [email]
        return await self._request("POST", f"{API_BASE_URL}/clients", json_body=body)

    async def create_appointment(
        self,
        *,
        client_id: str,
        professional_id: str,
        service_id: str,
        location: str,
        start_iso: str,
    ) -> dict:
        """Створює appointment у CRM. start_iso у форматі '2026-04-15T14:00:00.000Z'."""
        body = {
            "client": client_id,
            "location": location,
            "services": [
                {
                    "professional": professional_id,
                    "service": service_id,
                    "start": start_iso,
                }
            ],
        }
        return await self._request("POST", f"{API_BASE_URL}/appointments", json_body=body)

    async def cancel_appointment(self, appointment_id: str, reason: str = "Cancelled by client") -> dict:
        return await self._request(
            "PUT",
            f"{API_BASE_URL}/appointments/{appointment_id}",
            params={"force": "true"},
            json_body={"state": "cancelled", "cancelReason": reason},
        )

    async def get_client_history(self, client_id: str) -> list[dict]:
        data = await self._request(
            "GET",
            f"{API_BASE_URL}/clients/{client_id}/history",
            params={
                "fields": (
                    "date,duration,professional,professional_name,paid,"
                    "items(id,name,type,picture,quantity,sum,sale_id),feedback"
                )
            },
        )
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not isinstance(data, list):
            data = [data] if data else []
        return data
