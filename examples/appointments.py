"""Utility script to fetch client data for each salon."""

from __future__ import annotations

import json
import requests
import time
from typing import Dict, Any
from datetime import date, datetime

from config import AUTH_LOCATION_ID, get_salon_data_dir, iter_salons

# Constants
API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10  # seconds
SALON_CLIENT="88ddf545-e5c4-1303-201d-81a3624f9319"
#"88de1d30-7ce5-36fc-2238-0d906449dce2"
# Configuration shared across requests
AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": AUTH_LOCATION_ID,
}


def _request_with_retries(url: str, **kwargs) -> requests.Response:
    """Make a GET request with retries."""

    for attempt in range(4):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            if attempt == 3:
                raise exc
            time.sleep(60)


def get_access_token(database_code: str) -> str:
    """Получает access token."""

    params = AUTH_PARAMS.copy()
    params["database_code"] = database_code
    try:
        response = _request_with_retries(
            f"{API_BASE_URL}/auth/database",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        return response.json()["access_token"]
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network call
        raise RuntimeError(f"Authentication failed: {exc}") from exc


def fetch_api_data(url: str, token: str, params: Dict[str, Any] | None = None) -> Any:
    """Запрашивает данные по API и возвращает JSON."""

    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = _request_with_retries(
            url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network call
        raise RuntimeError(f"API request failed: {exc}") from exc


def cancel_appointment(appointment_id: str, database_code: str, cancel_reason: str = "Cancelled by client") -> Dict[str, Any]:
    """Скасовує запис в CRM.
    
    Parameters
    ----------
    appointment_id : str
        ID запису в CRM
    database_code : str
        Код бази даних салону
    cancel_reason : str
        Причина скасування
        
    Returns
    -------
    Dict[str, Any]
        Результат операції
    """
    token = get_access_token(database_code)
    
    url = f"{API_BASE_URL}/appointments/{appointment_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "state": "cancelled",
        "cancelReason": cancel_reason
    }
    
    for attempt in range(4):
        try:
            response = requests.put(
                url,
                headers=headers,
                json=payload,
                params={"force": "true"},
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"Failed to cancel appointment: {exc}") from exc
            time.sleep(60)


def reschedule_appointment_service(
    service_id: str,
    database_code: str,
    new_date: str,
    new_time: str,
    new_professional_id: str | None = None
) -> Dict[str, Any]:
    """Переносить запис на іншу дату/час/майстра.
    
    Parameters
    ----------
    service_id : str
        ID послуги в записі
    database_code : str
        Код бази даних салону
    new_date : str
        Нова дата (YYYY-MM-DD)
    new_time : str
        Новий час (HH:MM)
    new_professional_id : str | None
        ID нового майстра (якщо потрібно змінити)
        
    Returns
    -------
    Dict[str, Any]
        Результат операції
    """
    token = get_access_token(database_code)
    
    url = f"{API_BASE_URL}/appointments/services/{service_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Формуємо start у форматі ISO
    start = f"{new_time}:00.000Z"
    
    payload = {"start": start}
    
    if new_professional_id:
        payload["professional"] = new_professional_id
    
    for attempt in range(4):
        try:
            response = requests.put(
                url,
                headers=headers,
                json=payload,
                params={
                    "force": "true",
                    "fields": "start,duration,professional,service,appointment,state"
                },
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"Failed to reschedule appointment: {exc}") from exc
            time.sleep(60)


def main() -> None:
    try:
        for salon in iter_salons():
            print(date.today().isoformat()+"T07:00:00.000Z")
            access_token = get_access_token(salon["database_code"])
            print(salon["location_sales"])
            appointments_services_data = fetch_api_data(
                f"{API_BASE_URL}/appointments/services",
                access_token,
                params={
                    "fields": (
                        "start,duration,professional,professionalName,professionalPhone,service,price,serviceName,quantity,appointment,state,cancelReason,client,clientName,clientPhone,location"
                    ),
                    "from": date.today().isoformat()+"T07:00:00.000Z",
                    "location": salon["location_sales"],
                    "client": SALON_CLIENT
                },
            )
            print(f"\nAppointments Services: {appointments_services_data}")

            # 1) Зібрати всі унікальні appointment_id з масиву послуг
            appointment_ids = {
                item.get("appointment")
                for item in appointments_services_data
                if isinstance(item, dict) and item.get("appointment")
            }

            if not appointment_ids:
                print("Немає жодного appointment_id у відповіді / нічого підтягувати.")
            else:
                print(f"Знайдено {len(appointment_ids)} унікальних appointment_id.")

            # 2) Для кожного appointment_id — окремий запит на /appointments/{id}
            for idx, appointment_id in enumerate(sorted(appointment_ids), start=1):
                try:
                    clients_data = fetch_api_data(
                        f"{API_BASE_URL}/appointments/{appointment_id}",
                        access_token,
                        params={
                            "fields": (
                                "date,state,location,client,clientName,employee,employeeName,"
                                "receptionist,cancelDate,cancelReason"
                            )
                        },
                    )
                    print(f"\n[{idx}/{len(appointment_ids)}] Appointment {appointment_id}: {clients_data}")
                except RuntimeError as exc:
                    # Локальний хендлінг, щоб падіння одного не зривало цикл
                    print(f"[{idx}/{len(appointment_ids)}] Помилка для {appointment_id}: {exc}")


    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error occurred: {exc}")


if __name__ == "__main__":
    main()


__all__ = [
    "get_access_token",
    "fetch_api_data",
    "cancel_appointment",
    "reschedule_appointment_service",
]







