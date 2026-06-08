
import os
import requests
import json
from datetime import datetime
from dateutil.relativedelta import relativedelta  # pip install python-dateutil

from _chat_db import chat_conn, load_salons

API_BASE_URL = "https://api.aihelps.com/v1"
AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": "a47cbc05-5ce6-4551-9456-28ccb52bbb11",
}


def _load_salons_from_db():
    """Salon list from Postgres booking.salons (заміна старого MySQL salons)."""
    with chat_conn() as conn:
        return load_salons(conn)


def update_free_time(salon_code: str | None = None):
    """Refresh freetime.json. If salon_code given — only that salon (швидко).
    Без аргументу — всі салони (для cron / batch)."""
    today = datetime.today()
    two_months_later = today + relativedelta(months=+2)
    date_from = today.strftime('%Y-%m-%d')
    date_to = two_months_later.strftime('%Y-%m-%d')

    url_token = f"{API_BASE_URL}/auth/database"
    url_sales = f"{API_BASE_URL}/employees/free_time"

    all_salons = _load_salons_from_db()
    if salon_code:
        salons = [s for s in all_salons if s["code"] == salon_code]
        if not salons:
            print(f"[freetime] Unknown salon_code: {salon_code}")
            return {}
    else:
        salons = all_salons
    print(f"[freetime] Updating {len(salons)} salon(s)")

    results = {}

    for salon in salons:
        try:
            params_token = AUTH_PARAMS.copy()
            params_token["database_code"] = salon["database_code"]
            response_token = requests.get(url_token, params=params_token)
            response_token.raise_for_status()
            access_token = response_token.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}"}

            # Auto-discover CRM location ID
            resp_loc = requests.get(f"{API_BASE_URL}/locations?fields=name", headers=headers)
            resp_loc.raise_for_status()
            locations_data = resp_loc.json()
            if isinstance(locations_data, list) and len(locations_data) > 0:
                location_sales = locations_data[0]["id"]
            else:
                print(f"[freetime] {salon['code']}: no CRM locations found, skipping")
                continue

            params_sales = {
                "from": date_from,
                "to": date_to,
                "duration": "30",
                "step": "30m",
                "location": location_sales,
            }

            response_sales = requests.get(url_sales, headers=headers, params=params_sales)
            response_sales.raise_for_status()
            sales_data = response_sales.json()

            output_dir = os.path.join(os.path.dirname(__file__), "static", "data", salon["folder"])
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, "freetime.json")
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(sales_data, f, ensure_ascii=False, indent=4)

            results[salon["code"]] = sales_data
            print(f"[freetime] {salon['code']}: OK ({len(sales_data)} entries)")
        except Exception as e:
            print(f"[freetime] {salon['code']}: ERROR - {e}")

    return results


if __name__ == "__main__":
    update_free_time()
