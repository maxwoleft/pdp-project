"""Utility script to fetch only matched client data (by name/phone/email) for each salon,
including visit history, with console progress output. First loads minimal fields, then expands only matches.
"""

from __future__ import annotations

import json
import re
import requests
import time
from typing import Dict, Any, List

from config import AUTH_LOCATION_ID, get_salon_data_dir, iter_salons

API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10

AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "location": AUTH_LOCATION_ID,
}

# === ПОШУКОВІ ПАРАМЕТРИ (заповни вручну) ===
SEARCH_NAMES = ["Марія"]          # частина або повне ім’я
SEARCH_PHONES = ["0638622062"]   # частина або повний номер
SEARCH_EMAILS = []  # частина або повний email
SEARCH_QUERY = None  # універсальний рядок, додається до всіх трьох
# ===========================================


def _request_with_retries(url: str, **kwargs) -> requests.Response:
    for attempt in range(4):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            if attempt == 3:
                raise exc
            print(f"[retry] {url} → спроба {attempt + 1}, чекаємо 60s…")
            time.sleep(60)


def get_access_token(database_code: str) -> str:
    params = AUTH_PARAMS.copy()
    params["database_code"] = database_code
    print(f"[auth] Отримуємо токен для бази: {database_code}")
    response = _request_with_retries(
        f"{API_BASE_URL}/auth/database",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Не вдалося отримати access_token")
    print(f"[auth] ✅ Токен отримано")
    return token


def fetch_api_data(url: str, token: str, params: Dict[str, Any] | None = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    response = _request_with_retries(
        url,
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    return response.json()


# ★ Мінімальний фетч: тільки те, що треба для фільтра (швидко й дешево)
def fetch_clients_minimal(token: str) -> List[Dict[str, Any]]:
    fields = "archive,name,phone,email"  # ★ потрібні поля
    data = fetch_api_data(f"{API_BASE_URL}/clients", token, params={"fields": fields})

    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        data = data["data"]
    if not isinstance(data, list):
        data = []

    # ★ фільтруємо лише активних клієнтів
    data = [c for c in data if not c.get("archive")]

    return data




# ★ Повні дані клієнта — витягуємо лише для збігів
def fetch_client_details(client_id: Any, token: str) -> Dict[str, Any]:
    fields = (
        "name,title,gender,birthday,balance,bonus,card_number,phone,email,"
        "categories,categories_names,first_visit,first_visit_description,last_visit,"
        "last_visit_description,feedback,additional_fields,deposit_client,referral_source,"
        "referral_source_name,status,archive,professional,comments"
    )
    url = f"{API_BASE_URL}/clients/{client_id}"
    data = fetch_api_data(url, token, params={"fields": fields})
    # дехто з API віддає як {"data": {...}}
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
        data = data["data"]
    if not isinstance(data, dict):
        data = {}
    return data


def numbers_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def to_list(val: Any) -> List[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def normalize_phones(raw: Any) -> List[str]:
    phones: List[str] = []
    for p in to_list(raw):
        if isinstance(p, dict):
            v = p.get("value") or p.get("phone") or p.get("number") or ""
        else:
            v = str(p)
        v = numbers_only(v)
        if v:
            phones.append(v)
    return phones


def normalize_emails(raw: Any) -> List[str]:
    emails: List[str] = []
    for e in to_list(raw):
        if isinstance(e, dict):
            v = e.get("value") or e.get("email") or ""
        else:
            v = str(e)
        v = v.strip().lower()
        if v:
            emails.append(v)
    return emails


def match_client(client: Dict[str, Any]) -> bool:
    name_val = (client.get("name") or "").strip().casefold()
    client_phones = normalize_phones(client.get("phone"))
    client_emails = normalize_emails(client.get("email"))

    # name match
    for n in SEARCH_NAMES:
        if str(n).strip().casefold() in name_val:
            return True

    # phone match
    for q in SEARCH_PHONES:
        q_digits = numbers_only(str(q))
        if not q_digits:
            continue
        if any(q_digits in p for p in client_phones):
            return True

    # email match
    for q in SEARCH_EMAILS:
        q_norm = str(q).strip().lower()
        if not q_norm:
            continue
        if any(q_norm in e for e in client_emails):
            return True

    # universal query
    if SEARCH_QUERY:
        q = str(SEARCH_QUERY).strip().lower()
        if q in name_val:
            return True
        if any(q in e for e in client_emails):
            return True
        if any(q in p for p in client_phones):
            return True

    return False


def fetch_client_history(client_id: Any, token: str) -> List[Dict[str, Any]]:
    if not client_id:
        return []
    url = f"{API_BASE_URL}/clients/{client_id}/history"
    params = {
        "fields": (
            "date,duration,professional,professional_name,paid,"
            "items(id,name,type,picture,quantity,sum,sale_id),feedback"
        )
    }
    data = fetch_api_data(url, token, params=params)
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        data = data["data"]
    if not isinstance(data, list):
        data = [data]
    return data or []


def main() -> None:
    # комбінуємо універсальний рядок у всі групи
    names = SEARCH_NAMES[:]
    phones = SEARCH_PHONES[:]
    emails = SEARCH_EMAILS[:]
    if SEARCH_QUERY:
        names.append(SEARCH_QUERY)
        phones.append(SEARCH_QUERY)
        emails.append(SEARCH_QUERY)

    print("\n=== Режим пошуку ===")
    print(f"Імена: {names or '-'}")
    print(f"Телефони: {phones or '-'}")
    print(f"Email-и: {emails or '-'}")
    print("====================\n")

    try:
        for salon in iter_salons():
            print(f"\n=== Обробка салону: ({salon['database_code']}) ===")
            token = get_access_token(salon["database_code"])

            # ★ Тягнемо тільки мінімальні поля для фільтра
            print("[fetch] Завантажуємо «легкий» список клієнтів…")
            minimal_clients = fetch_clients_minimal(token)
            print(f"[fetch] ✅ Отримано {len(minimal_clients)} записів (мінімальні поля)")

            # фільтруємо збіги (ігноруємо архівних)
            filtered_ids: List[Any] = []
            for c in minimal_clients:
                if c.get("archive"):
                    continue
                if match_client(c):
                    cid = c.get("id")
                    if cid is not None:
                        filtered_ids.append(cid)

            print(f"[filter] ✅ Знайдено збігів: {len(filtered_ids)}")

            # ★ Для кожного збігу тягнемо повні дані + історію
            matched_full: List[Dict[str, Any]] = []
            for idx, cid in enumerate(filtered_ids, start=1):
                print(f"   → [{idx}/{len(filtered_ids)}] id={cid} — деталі + історія… ", end="", flush=True)
                try:
                    details = fetch_client_details(cid, token)  # ★ повний профіль
                    history = fetch_client_history(cid, token)
                    details["history"] = history
                    matched_full.append(details)
                    print(f"✅ історія {len(history)} записів")
                except Exception as exc:
                    # навіть якщо історія впала — принаймні збережемо деталі
                    try:
                        details = fetch_client_details(cid, token)
                    except Exception:
                        details = {"id": cid}
                    details["history"] = []
                    matched_full.append(details)
                    print(f"⚠️  помилка: {exc}")

                if idx % 20 == 0:
                    time.sleep(0.2)

            output_file = get_salon_data_dir(salon["folder"]) / "clients_search.json"
            with output_file.open("w", encoding="utf-8") as fh:
                json.dump(matched_full, fh, ensure_ascii=False, indent=4)
            print(f"[save] ✅ Збережено результати у {output_file}")

    except Exception as exc:
        print(f"[fatal] ❌ Виникла помилка: {exc}")


if __name__ == "__main__":
    main()
