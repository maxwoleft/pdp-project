"""Каталог усіх салонів мережі.

Кожен запис → один салон у БД. UUID детермінований (uuid5 від database_code),
щоб повторні запуски seed/sync не створювали дублі.
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
    address: str | None = None
    phone: str | None = None
    location_sales: str | None = None  # для freetime, якщо відрізняється від default

    @property
    def salon_id(self) -> str:
        return str(uuid.uuid5(SALON_UUID_NAMESPACE, f"salon:{self.database_code}"))


SALONS: list[SalonConfig] = [
    # ── UA: Київ ───────────────────────────────────────────────
    SalonConfig("ua", "Київ", "Бесарабка", "362663", "Europe/Kyiv"),
    SalonConfig("ua", "Київ", "Ахматова", "229710", "Europe/Kyiv"),
    SalonConfig(
        "ua", "Київ", "Яр Вал", "696346", "Europe/Kyiv",
        location_sales="88d6f0d6-20e3-47e0-02b7-e2eb71d5fa5a",  # PIED-DE-POULE Yar.Val
    ),
    SalonConfig("ua", "Київ", "Оболонь", "378459", "Europe/Kyiv"),
    SalonConfig("ua", "Київ", "Богдана Хмельницького", "943418", "Europe/Kyiv"),
    # ── UA: Буча ───────────────────────────────────────────────
    SalonConfig("ua", "Буча", "Bucha", "909369", "Europe/Kyiv"),
    # ── UA: Харків ─────────────────────────────────────────────
    SalonConfig("ua", "Харків", "Героїв Харкова", "183474", "Europe/Kyiv"),
    SalonConfig("ua", "Харків", "Науки", "172964", "Europe/Kyiv"),
    # ── UA: Одеса ──────────────────────────────────────────────
    SalonConfig("ua", "Одеса", "Odesa", "566065", "Europe/Kyiv"),
    SalonConfig("ua", "Одеса", "Odesa 2", "591416", "Europe/Kyiv"),
    # ── UA: Ужгород ────────────────────────────────────────────
    SalonConfig("ua", "Ужгород", "Ужгород", "354175", "Europe/Kyiv"),
    # ── UA: Володимир ──────────────────────────────────────────
    SalonConfig("ua", "Володимир", "ВВ", "812847", "Europe/Kyiv"),

    # ── PL: Варшава ────────────────────────────────────────────
    SalonConfig("pl", "Warszawa", "Wilanow", "761771", "Europe/Warsaw"),
    SalonConfig("pl", "Warszawa", "Grzybowska 4", "518553", "Europe/Warsaw"),
    SalonConfig("pl", "Warszawa", "Piekna", "830688", "Europe/Warsaw"),
    # ── PL: Вроцлав ────────────────────────────────────────────
    SalonConfig("pl", "Wrocław", "Wrocław", "933845", "Europe/Warsaw"),

    # ── GB: London ─────────────────────────────────────────────
    SalonConfig(
        "gb", "London", "Oxford Circus", "776611", "Europe/London",
        address="67 Mortimer Street, London, W1W 7SE",
        phone="+44 7907 707767",
    ),
    SalonConfig(
        "gb", "London", "South Kensington", "703835", "Europe/London",
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
