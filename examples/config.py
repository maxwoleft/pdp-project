
"""Shared configuration for salon data scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable

DATA_ROOT = Path(__file__).resolve().parent

# Location identifier required for authentication requests.
AUTH_LOCATION_ID = "a47cbc05-5ce6-4551-9456-28ccb52bbb11"

# Default location for free-time lookups when a salon does not override it.
DEFAULT_FREETIME_LOCATION_ID = "a47cbc05-5ce6-4551-9456-28ccb52bbb11"

# Centralised list of salons. Add new entries here when onboarding a salon.
SALONS: list[Dict[str, str | None]] = [
    {
        "folder": "salon1",
        "database_code": "776611",
        "location_sales": "88dc7410-8542-84ac-4594-52121b277511",
    },
    {
        "folder": "salon2",
        "database_code": "703835",
        "location_sales": "88dd9798-2767-0e1c-7240-e19255e8e78d",
    },
]


def get_salon_data_dir(folder: str) -> Path:
    """Return the directory where generated JSON files for a salon are stored."""

    path = DATA_ROOT / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def iter_salons() -> Iterable[Dict[str, str | None]]:
    """Yield salon configurations."""

    return iter(SALONS)

