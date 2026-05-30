"""Fetch services data for every configured salon."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict

import requests

from config import AUTH_LOCATION_ID, get_salon_data_dir, iter_salons

API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10  # seconds

AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": AUTH_LOCATION_ID,
}


def _request_with_retries(url: str, **kwargs) -> requests.Response:
    """Make a GET request with retries."""

    for attempt in range(3):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            if attempt == 2:
                raise exc
            if attempt == 1:
                time.sleep(60)


def get_access_token(database_code: str) -> str:
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



def clean_description(desc: str) -> str:
    """Удаляет все HTML-теги, кроме <br>, которые заменяет на символ новой строки."""

    if not desc:
        return ""
    desc = re.sub(r"<br\s*/?>", "\n", desc, flags=re.IGNORECASE)
    desc = re.sub(r"<.*?>", "", desc)
    return desc.strip()


def main() -> None:
    try:
        for salon in iter_salons():
            access_token = get_access_token(salon["database_code"])

            sales_data = fetch_api_data(
                f"{API_BASE_URL}/services",
                access_token,
                params={
                    "fields": "name,description,duration,price_currency,category,location_prices,archive",
                },
            )

            active_services = [service for service in sales_data if service.get("archive") is False]

            # Фільтруємо add-on послуги (не можуть бути основною послугою для запису)
            def is_addon(service: Dict[str, Any]) -> bool:
                name = (service.get("name") or "").lower()
                return "add-on" in name or "addon" in name

            active_services = [s for s in active_services if not is_addon(s)]

            category_cache: Dict[str, Dict[str, Any]] = {}

            for service in active_services:
                if "location_prices" in service and isinstance(service["location_prices"], list):
                    lp_item = next((lp for lp in service["location_prices"] if isinstance(lp, dict)), None)
                    if lp_item:
                        service["location_prices"] = lp_item.get("price")
                        service["location_position"] = lp_item.get("position")
                    else:
                        service["location_prices"] = None
                        service["location_position"] = None

                category_id = service.get("category")
                if category_id:
                    if category_id in category_cache:
                        cat_data = category_cache[category_id]
                    else:
                        category_url = f"{API_BASE_URL}/services/categories/{category_id}"
                        cat_data = fetch_api_data(
                            category_url,
                            access_token,
                            params={"fields": "name,parent,picture,archive"},
                        )
                        category_cache[category_id] = cat_data

                    service["category"] = cat_data.get("name", category_id)

                    parent_id = cat_data.get("parent")
                    if parent_id:
                        if parent_id in category_cache:
                            parent_data = category_cache[parent_id]
                        else:
                            parent_url = f"{API_BASE_URL}/services/categories/{parent_id}"
                            parent_data = fetch_api_data(
                                parent_url,
                                access_token,
                                params={"fields": "name,parent,picture,archive"},
                            )
                            category_cache[parent_id] = parent_data
                        service["parent"] = parent_data.get("name", parent_id)
                    else:
                        service["parent"] = None

                if "description" in service:
                    service["description"] = clean_description(service["description"])

            # Фільтруємо add-on по категорії (після того як category name отримано)
            def is_addon_category(service: Dict[str, Any]) -> bool:
                category = (service.get("category") or "").lower()
                parent = (service.get("parent") or "").lower()
                return "add-on" in category or "addon" in category or "add-on" in parent or "addon" in parent

            active_services = [s for s in active_services if not is_addon_category(s)]
            print(f"[{salon['folder']}] Після фільтрації add-on: {len(active_services)} послуг")

            output_file = get_salon_data_dir(salon["folder"]) / "services.json"
            with output_file.open("w", encoding="utf-8") as fh:
                json.dump(active_services, fh, ensure_ascii=False, indent=4)
            print(f"\nДанные успешно сохранены в {output_file}")

    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error occurred: {exc}")


if __name__ == "__main__":
    main()