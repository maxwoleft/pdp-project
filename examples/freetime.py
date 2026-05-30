"""Fetch free time slots for configured salons."""

from __future__ import annotations

import json
from datetime import datetime

import requests
from dateutil.relativedelta import relativedelta  # pip install python-dateutil

from config import (
    AUTH_LOCATION_ID,
    DEFAULT_FREETIME_LOCATION_ID,
    get_salon_data_dir,
    iter_salons,
)

TOKEN_URL = "https://api.aihelps.com/v1/auth/database"
FREETIME_URL = "https://api.aihelps.com/v1/employees/free_time"

AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": AUTH_LOCATION_ID,
}


def update_free_time(salon_folder: str = None) -> dict[str, dict]:
    """Update free time files for salon(s) and return collected data.

    Args:
        salon_folder: If provided, only update this salon. Otherwise update all.
    """

    today = datetime.today()
    two_months_later = today + relativedelta(months=+2)
    date_from = today.strftime("%Y-%m-%d")
    date_to = two_months_later.strftime("%Y-%m-%d")

    results: dict[str, dict] = {}

    salons_to_update = []
    for salon in iter_salons():
        if salon_folder is None or salon["folder"] == salon_folder:
            salons_to_update.append(salon)

    for salon in salons_to_update:
        params_token = AUTH_PARAMS.copy()
        params_token["database_code"] = salon["database_code"]

        response_token = requests.get(TOKEN_URL, params=params_token)
        response_token.raise_for_status()
        access_token = response_token.json()["access_token"]

        headers_sales = {"Authorization": f"Bearer {access_token}"}
        location_sales = salon.get("location_sales") or DEFAULT_FREETIME_LOCATION_ID
        params_sales = {
            "from": date_from,
            "to": date_to,
            "duration": "30",
            "step": "15m",
            "location": location_sales,
        }

        response_sales = requests.get(FREETIME_URL, headers=headers_sales, params=params_sales)
        response_sales.raise_for_status()
        sales_data = response_sales.json()

        output_file = get_salon_data_dir(salon["folder"]) / "freetime.json"
        with output_file.open("w", encoding="utf-8") as fh:
            json.dump(sales_data, fh, ensure_ascii=False, indent=4)

        results[salon["folder"]] = sales_data
    print("Free time slots updated for salons:", list(results.keys()))
    return results


if __name__ == "__main__":
    update_free_time()