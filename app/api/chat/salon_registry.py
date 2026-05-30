"""Mapping salon UUID ↔ slug/country/city.

Використовується endpoint'ом `/chat/start` коли booking_london_predict шле
користувача напряму у конкретний салон. UUID — джерело правди (з нашої PG),
slug — booking-friendly код для людини.

При додаванні нового салону:
1. SELECT id, name, city FROM <country>.salon WHERE archive=false;
2. Додати запис тут.
3. У booking — задати `ai_chat_uuid` у `static/data/salons/<slug>/data-info.json`.

Якщо лінк прийшов з невідомим UUID/slug — endpoint поверне 404.
"""
from __future__ import annotations

SALON_REGISTRY: dict[str, dict[str, str]] = {
    # ── GB ───────────────────────────────────────────────────────────
    "72b6595b-622b-51f8-becd-c1f8f9545ce1": {
        "country": "gb", "city": "London", "slug": "london-mortimer",
        "name": "Oxford Circus",
    },
    "547d7d8a-286f-5169-9759-f27d2efc7d84": {
        "country": "gb", "city": "London", "slug": "london-brompton",
        "name": "South Kensington",
    },
    # ── PL ───────────────────────────────────────────────────────────
    "9c7afbb1-92b1-50bc-8c11-109140b3c487": {
        "country": "pl", "city": "Warszawa", "slug": "warsaw-grzybowska",
        "name": "Grzybowska 4",
    },
    "42c39620-e80b-5177-9017-d76830e81bd5": {
        "country": "pl", "city": "Warszawa", "slug": "warsaw-piekna",
        "name": "Piękna",
    },
    "28a58897-1a03-5d9b-98f8-6709b08bf201": {
        "country": "pl", "city": "Warszawa", "slug": "warsaw-wilanow",
        "name": "Wilanów",
    },
    "d773f2a9-9ffd-5cac-932d-ad4e94d93839": {
        "country": "pl", "city": "Wrocław", "slug": "wroclaw-kosciuszko",
        "name": "Wrocław",
    },
    # ── UA ───────────────────────────────────────────────────────────
    "8fb34cf2-175a-5366-ad2f-b563b898b02e": {
        "country": "ua", "city": "Буча", "slug": "bucha-avenir",
        "name": "Bucha",
    },
    "f31785b7-2871-5b72-9afd-8496272c93e7": {
        "country": "ua", "city": "Володимир", "slug": "volodymyr-center",
        "name": "Володимир",
    },
    "ac5ede17-0616-5463-b63d-571b69a17bcc": {
        "country": "ua", "city": "Київ", "slug": "kyiv-golden-gates",
        "name": "Ахматова",
    },
    "3a25518e-4d32-5ebf-94ea-fbf8282bc672": {
        "country": "ua", "city": "Київ", "slug": "kyiv-bessarabka",
        "name": "Бесарабка",
    },
    "b47a5a93-a1e2-5828-a4df-7febdcdb9a22": {
        "country": "ua", "city": "Київ", "slug": "kyiv-khmelnytsky",
        "name": "Богдана Хмельницького",
    },
    "fc2968cc-8131-5466-8960-6094b78a0e8f": {
        "country": "ua", "city": "Київ", "slug": "kyiv-obolon",
        "name": "Оболонь",
    },
    "aa6b1bf6-9ed0-506d-8e1e-0abc34c04166": {
        "country": "ua", "city": "Київ", "slug": "kyiv-poznyaky",
        "name": "Яр Вал",
    },
    "000bb780-8247-50c7-af7b-a3525a9d0431": {
        "country": "ua", "city": "Одеса", "slug": "odesa-karavanskoho",
        "name": "Odesa",
    },
    "8089c81f-f87f-51d0-91ce-5c389b083ed2": {
        "country": "ua", "city": "Одеса", "slug": "odesa-lvivska",
        "name": "Odesa 2",
    },
    "6a8ca78e-d63a-5976-86f7-7a7aaad5f039": {
        "country": "ua", "city": "Ужгород", "slug": "uzhhorod-dream-city",
        "name": "Ужгород",
    },
    "b56cbfad-d291-5626-8d95-87be2de1b241": {
        "country": "ua", "city": "Харків", "slug": "kharkiv-heroiv",
        "name": "Героїв Харкова",
    },
    "0d8207b7-1634-51db-af4e-6408df3fa89b": {
        "country": "ua", "city": "Харків", "slug": "kharkiv-nauki",
        "name": "Науки",
    },
}

# Reverse lookup: slug → uuid
SLUG_TO_UUID: dict[str, str] = {info["slug"]: uuid for uuid, info in SALON_REGISTRY.items()}


def lookup_by_uuid(salon_id: str) -> dict | None:
    return SALON_REGISTRY.get(salon_id)


def lookup_by_slug(slug: str) -> tuple[str, dict] | None:
    """Returns (uuid, info) or None."""
    uuid = SLUG_TO_UUID.get(slug)
    if not uuid:
        return None
    return uuid, SALON_REGISTRY[uuid]
