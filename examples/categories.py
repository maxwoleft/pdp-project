"""Fetch category data for all configured salons."""

from __future__ import annotations

import json
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

# def display_dataframe(df: pd.DataFrame) -> None:
#     """Выводит DataFrame с полным отображением столбцов."""
#     with pd.option_context('display.max_columns', None, 'display.expand_frame_repr', False):
#         print("\nData Types:")
#         print(df.dtypes)
#         print("\nData Contents:")
#         print(df)

def main() -> None:
    try:
        for salon in iter_salons():
            access_token = get_access_token(salon["database_code"])

            categories_data = fetch_api_data(
                f"{API_BASE_URL}/services/categories",
                access_token,
                params={
                    "fields": "name,parent,picture,archive",
                    "archive": "false",
                },
            )

            categories = categories_data
            categories_map: Dict[str, Dict[str, Any]] = {}
            for cat in categories:
                cat_id = cat.get("id")
                if cat_id:
                    categories_map[cat_id] = cat

            for cat in categories:
                parent_id = cat.get("parent")
                if parent_id and parent_id in categories_map:
                    cat["parentName"] = categories_map[parent_id].get("name")
                else:
                    cat["parentName"] = None

            output_file = get_salon_data_dir(salon["folder"]) / "categories.json"
            with output_file.open("w", encoding="utf-8") as fh:
                json.dump(categories, fh, ensure_ascii=False, indent=4)
            print(f"\nДанные успешно сохранены в {output_file}")

    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error occurred: {exc}")


if __name__ == "__main__":
    main()