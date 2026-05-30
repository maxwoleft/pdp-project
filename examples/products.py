"""Utility script to fetch client data for each salon."""

from __future__ import annotations

import json
import requests
import time
from typing import Dict, Any

from config import AUTH_LOCATION_ID, get_salon_data_dir, iter_salons

# Constants
API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10  # seconds

# Configuration shared across requests
AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": AUTH_LOCATION_ID,
}

EXCLUDED_EMPLOYEE_NAME_KEYWORDS = ("співробітники",)


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


def is_excluded_employee(employee: Dict[str, Any]) -> bool:
    """Return True if an employee should be excluded from export."""
    name = str(employee.get("name", "")).casefold()
    return any(keyword in name for keyword in EXCLUDED_EMPLOYEE_NAME_KEYWORDS)


# ===== Категорії =====

def get_categories_data(access_token: str) -> list[dict[str, Any]]:
    """Отримує всі категорії. Повертає список словників з щонайменше полями id та name."""
    url_categories = f"{API_BASE_URL}/products/categories"
    headers = {"Authorization": f"Bearer {access_token}"}
    # просимо і id, і name — id потрібен для мапінгу
    params = {"fields": "name"}
    response = _request_with_retries(url_categories, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def build_category_map(categories: list[dict[str, Any]]) -> dict[str, str]:
    """Робить мапу {category_id: category_name}. Строкові ключі — безпечніше для JSON."""
    cat_map: dict[str, str] = {}
    for c in categories or []:
        cid = c.get("id")
        name = c.get("name")
        if cid is None:
            continue
        # нормалізуємо id до str (на випадок якщо воно числове)
        cat_map[str(cid)] = name or ""
    return cat_map


def enrich_product_with_category(product: dict[str, Any], cat_map: dict[str, str]) -> dict[str, Any]:
    """Перетворює поле product['category'] з id у об'єкт {id, name} без втрати id."""
    cat_id = product.get("category")
    # Нормалізуємо до str для пошуку в мапі
    cat_name = cat_map.get(str(cat_id)) if cat_id is not None else None
    product["category_id"] = product.get("category")
    product["category_name"] = cat_map.get(str(product["category_id"]))
    return product


# ===== Утиліти для JSON =====

def convert_to_json(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def main() -> None:
    try:
        for salon in iter_salons():
            access_token = get_access_token(salon["database_code"])

            # 1) тягнемо продукти
            products = fetch_api_data(
                f"{API_BASE_URL}/products",
                access_token,
                params={
                    "fields": (
                        "name,description,category,vendor_code,barcode,volume,"
                        "supply_price,supply_price_currency,units,location_prices,"
                        "portion_quantity,stocks,tare_weight,critical_quantity,requisites,"
                        "is_receipt,product_receipts,count_as_cost_for_salary,tax,department,"
                        "price_for_salary_calculation,payed_calculation,consignment_supplier,archive"
                    )
                },
            )

            # 2) фільтр архівних/службових
            products = [
                p for p in products
                if not p.get("archive", False) and not is_excluded_employee(p)
            ]

            # 3) тягнемо категорії та будуємо мапу
            categories = get_categories_data(access_token)
            cat_map = build_category_map(categories)

            # 4) збагачуємо кожен продукт назвою категорії
            products = [enrich_product_with_category(p, cat_map) for p in products]

            # 5) збереження
            output_file = get_salon_data_dir(salon["folder"]) / "products.json"
            with output_file.open("w", encoding="utf-8") as fh:
                json.dump(products, fh, ensure_ascii=False, indent=4)

            print(f"\nДані успішно збережені в {output_file}")

    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error occurred: {exc}")


if __name__ == "__main__":
    main()
