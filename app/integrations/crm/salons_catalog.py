"""Каталог усіх салонів мережі.

Кожен запис → один салон у БД. UUID детермінований (uuid5 від database_code),
щоб повторні запуски seed/sync не створювали дублі.

Поля code/data_dir/location_slug/sort_order/status експортуються у Salon і
використовуються booking_london_predict (сторонній Flask-додаток), щоб тягнути
JSON з нашого Postgres замість AIHelps CRM.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

# Namespace для генерації детермінованих UUID салонів
SALON_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # стандартний DNS namespace


@dataclass(frozen=True)
class SalonConfig:
    country: str          # ua / pl / gb
    city: str
    name: str
    database_code: str
    timezone: str
    code: str             # публічний код для booking (k1, l1, ...)
    data_dir: str         # папка для JSON у booking (london-mortimer, ...)
    location_slug: str    # slug міста для booking (london, kyiv, ...)
    sort_order: int
    status: str = "active"   # active|planned|inactive
    address: str | None = None
    phone: str | None = None
    location_sales: str | None = None  # для freetime, якщо відрізняється від default

    @property
    def salon_id(self) -> str:
        return str(uuid.uuid5(SALON_UUID_NAMESPACE, f"salon:{self.database_code}"))


SALONS: list[SalonConfig] = [
    # ── UA: Київ ───────────────────────────────────────────────
    SalonConfig("ua", "Київ", "Бесарабка", "362663", "Europe/Kyiv",
                code="k1", data_dir="kyiv-bessarabka", location_slug="kyiv", sort_order=1),
    SalonConfig("ua", "Київ", "Ахматова", "229710", "Europe/Kyiv",
                code="k3", data_dir="kyiv-poznyaky", location_slug="kyiv", sort_order=3),
    SalonConfig("ua", "Київ", "Яр Вал", "696346", "Europe/Kyiv",
                code="k4", data_dir="kyiv-golden-gates", location_slug="kyiv", sort_order=4,
                location_sales="88d6f0d6-20e3-47e0-02b7-e2eb71d5fa5a"),
    SalonConfig("ua", "Київ", "Оболонь", "378459", "Europe/Kyiv",
                code="k5", data_dir="kyiv-obolon", location_slug="kyiv", sort_order=5),
    SalonConfig("ua", "Київ", "Богдана Хмельницького", "943418", "Europe/Kyiv",
                code="k2", data_dir="kyiv-khmelnytsky", location_slug="kyiv", sort_order=2),
    # ── UA: Буча ───────────────────────────────────────────────
    SalonConfig("ua", "Буча", "Bucha", "909369", "Europe/Kyiv",
                code="bu1", data_dir="bucha-avenir", location_slug="bucha", sort_order=1),
    # ── UA: Харків ─────────────────────────────────────────────
    SalonConfig("ua", "Харків", "Героїв Харкова", "183474", "Europe/Kyiv",
                code="kh2", data_dir="kharkiv-heroiv", location_slug="kharkiv", sort_order=2),
    SalonConfig("ua", "Харків", "Науки", "172964", "Europe/Kyiv",
                code="kh1", data_dir="kharkiv-nauki", location_slug="kharkiv", sort_order=1),
    # ── UA: Одеса ──────────────────────────────────────────────
    SalonConfig("ua", "Одеса", "Odesa", "566065", "Europe/Kyiv",
                code="od1", data_dir="odesa-karavanskoho", location_slug="odesa", sort_order=1),
    SalonConfig("ua", "Одеса", "Odesa 2", "591416", "Europe/Kyiv",
                code="od2", data_dir="odesa-lvivska", location_slug="odesa", sort_order=2),
    # ── UA: Ужгород ────────────────────────────────────────────
    SalonConfig("ua", "Ужгород", "Ужгород", "354175", "Europe/Kyiv",
                code="uz1", data_dir="uzhhorod-dream-city", location_slug="uzhhorod", sort_order=1),
    # ── UA: Володимир ──────────────────────────────────────────
    SalonConfig("ua", "Володимир", "ВВ", "812847", "Europe/Kyiv",
                code="vl1", data_dir="volodymyr-center", location_slug="volodymyr", sort_order=1),

    # ── PL: Варшава ────────────────────────────────────────────
    SalonConfig("pl", "Warszawa", "Wilanow", "761771", "Europe/Warsaw",
                code="wa3", data_dir="warsaw-wilanow", location_slug="warsaw", sort_order=3),
    SalonConfig("pl", "Warszawa", "Grzybowska 4", "518553", "Europe/Warsaw",
                code="wa2", data_dir="warsaw-grzybowska", location_slug="warsaw", sort_order=2),
    SalonConfig("pl", "Warszawa", "Piekna", "830688", "Europe/Warsaw",
                code="wa1", data_dir="warsaw-piekna", location_slug="warsaw", sort_order=1),
    # ── PL: Вроцлав ────────────────────────────────────────────
    SalonConfig("pl", "Wrocław", "Wrocław", "933845", "Europe/Warsaw",
                code="wr1", data_dir="wroclaw-kosciuszko", location_slug="wroclaw", sort_order=1),

    # ── GB: London ─────────────────────────────────────────────
    SalonConfig(
        "gb", "London", "Oxford Circus", "776611", "Europe/London",
        code="l1", data_dir="london-mortimer", location_slug="london", sort_order=1,
        address="67 Mortimer Street, London, W1W 7SE",
        phone="+44 7907 707767",
    ),
    SalonConfig(
        "gb", "London", "South Kensington", "703835", "Europe/London",
        code="l2", data_dir="london-brompton", location_slug="london", sort_order=2,
        address="62 Old Brompton Road, London, SW7 3LQ",
        phone="+44 7775 449715",
        location_sales="88dd9798-2767-0e1c-7240-e19255e8e78d",
    ),
]


def by_country(country: str) -> list[SalonConfig]:
    return [s for s in SALONS if s.country == country.lower()]


def by_database_code(code: str) -> SalonConfig | None:
    for s in SALONS:
        if s.database_code == code:
            return s
    return None


def by_salon_id(salon_id: str) -> SalonConfig | None:
    for s in SALONS:
        if s.salon_id == salon_id:
            return s
    return None


def by_code(code: str) -> SalonConfig | None:
    for s in SALONS:
        if s.code == code:
            return s
    return None
