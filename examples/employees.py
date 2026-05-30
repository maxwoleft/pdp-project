"""Utility script to fetch employee profiles for each salon."""

from __future__ import annotations

import json
import requests
import time
from typing import Dict, Any

from config import AUTH_LOCATION_ID, get_salon_data_dir, iter_salons

API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10  # seconds

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


def is_excluded_employee(employee: Dict[str, Any]) -> bool:
    name = str(employee.get("name", "")).casefold()
    return any(keyword in name for keyword in EXCLUDED_EMPLOYEE_NAME_KEYWORDS)


# def display_dataframe(df: pd.DataFrame) -> None:
#     with pd.option_context('display.max_columns', None, 'display.expand_frame_repr', False):
#         print("\nData Types:")
#         print(df.dtypes)
#         print("\nData Contents:")
#         print(df)


def main() -> None:
    try:
        for salon in iter_salons():
            access_token = get_access_token(salon["database_code"])

            employees_data = fetch_api_data(
                f"{API_BASE_URL}/employees",
                access_token,
                params={
                    "fields": (
                        "name,title,phone,email,photo_exists,photo,positions,position_names,roles," \
                        "archive,prepaymentRequired,comments,commentsPlainText"
                    )
                },
            )

            employees_data = [
                emp
                for emp in employees_data
                if not emp.get("archive", False) and not is_excluded_employee(emp)
            ]

            output_file = get_salon_data_dir(salon["folder"]) / "employees.json"
            with output_file.open("w", encoding="utf-8") as fh:
                json.dump(employees_data, fh, ensure_ascii=False, indent=4)
            print(f"\nДанные успешно сохранены в {output_file}")

    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error occurred: {exc}")


if __name__ == "__main__":
    main()