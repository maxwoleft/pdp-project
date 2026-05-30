"""Regression-тести для canonical_key + extract_attributes.

Кожен тест фіксує інваріант. Якщо змінюється алгоритм — тести
скажуть, які групи раптом склеїлись/розклеїлись.
"""
from __future__ import annotations

import pytest

from app.domain.services.canonical_key import (
    extract_attributes,
    normalize_to_canonical_key,
)


# ── Базові форми: довжина / рівень → один ключ ─────────────────────


@pytest.mark.parametrize(
    "names",
    [
        # Класичний випадок: 1-6 довжина × АРТ/МАЙСТЕР/ТОП → один ключ
        [
            "Стрижка жіноча 1 довжина АРТ",
            "Стрижка жіноча 2 довжина МАЙСТЕР",
            "Стрижка жіноча 3 довжина ТОП",
            "Стрижка жіноча 4 довжина МАЙСТЕР",
            "Стрижка жіноча 6 довжина ТОП",
            "Стрижка жіноча Топ майстер",
        ],
        # Блонд миття — з/без дефісу + всі довжини
        [
            "Блонд миття 1 довжина АРТ",
            "Блонд-миття 1 довжина АРТ",
            "Блонд миття 2 довжина МАЙСТЕР",
            "Блонд-миття 2 довжина МАЙСТЕР",
            "Блонд миття 4 довжина ТОП",
        ],
    ],
)
def test_length_and_level_collapse(names):
    """Той же ключ для різних довжин і рівнів майстра."""
    keys = {normalize_to_canonical_key(n) for n in names}
    assert len(keys) == 1, f"Expected 1 key, got {len(keys)}: {keys}"


# ── Порядок токенів ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name1,name2",
    [
        ("Лімфодренажний масаж", "Масаж лімфодренажний"),
        ("Медичний педикюр", "Педикюр медичний"),
        ("Чоловіча стрижка", "Стрижка чоловіча"),
        ("Японський чоловічий педикюр", "Чоловічий японський педикюр"),
        ("Глибоке бікіні", "Бікіні глибоке"),
        ("Весільна зачіска", "Зачіска весільна"),
        ("Класичний педикюр", "Педикюр класичний"),
        ("Алгінатна маска", "Маска алгінатна"),
        ("Ультразвукова чистка", "Чистка ультразвукова"),
        ("Вечірній макіяж", "Макіяж вечірній"),
    ],
)
def test_token_order_independence(name1, name2):
    """Різний порядок слів → той самий ключ."""
    k1 = normalize_to_canonical_key(name1)
    k2 = normalize_to_canonical_key(name2)
    assert k1 == k2, f"Different keys for token-equivalent names: {name1!r}={k1!r} vs {name2!r}={k2!r}"


# ── Multi-language CRM names ───────────────────────────────────────


def test_crm_multilang_uk_extracted():
    """UA частина CRM-назви домінує над EN/RUS."""
    name = "EN Hair cut / UA Стрижка / RUS Стрижка"
    key = normalize_to_canonical_key(name)
    assert key == "stryzhka", f"Expected 'stryzhka', got {key!r}"


# ── Бренди → окрема колонка ────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_brand",
    [
        ("Lebel Absolute Happiness 1 довжина МАЙСТЕР", "lebel"),
        ("Brae Power Dose 1 ампула МАЙСТЕР", "brae power dose"),
        ("Tokio Inkarami реконструкція волосся", "tokio inkarami"),
        ("LaBIOSTHETIQUE фарбування коренів", "la biosthetique"),
        ("LaBiosthetique фарбування 1 довжина", "la biosthetique"),
        ("La Biosthetique тонування", "la biosthetique"),
        ("Schwarskopf Vibrance тонування", "schwarzkopf"),  # опечатка → правильний
        ("Schwarzkopf Vibrance тонування", "schwarzkopf"),
        ("Айкун ліфтинг обличчя", "icoone"),
        ("Icoone laser масаж", "icoone"),
        ("Juvederm Volift 1 ml", "juvederm"),
        ("Belotero Balance 1 ml", "belotero"),
    ],
)
def test_brand_extracted(name, expected_brand):
    """Бренд переходить у окрему колонку, base_name без нього."""
    attrs = extract_attributes(name)
    assert attrs["brand"] == expected_brand, (
        f"Expected brand={expected_brand!r}, got {attrs['brand']!r} for {name!r}"
    )
    assert expected_brand.split()[0] not in attrs["base_name"], (
        f"Brand token leaked into base_name: {attrs['base_name']!r}"
    )


# ── Обʼєм у мл / ml → volume_ml колонка ────────────────────────────


@pytest.mark.parametrize(
    "name,expected_ml",
    [
        ("Філер для губ 1 мл", 1.0),
        ("Філер для губ 0,55мл", 0.55),
        ("Belotero Balance 1 ml", 1.0),
        ("Juvederm Ultra Smile 0.55 ml", 0.55),
        ("Belotero Shape 0,6 ml", 0.6),
        ("Біоревіталізація 2,5 мл", 2.5),
    ],
)
def test_volume_extracted(name, expected_ml):
    attrs = extract_attributes(name)
    assert attrs["volume_ml"] == expected_ml, (
        f"Expected volume_ml={expected_ml}, got {attrs['volume_ml']} for {name!r}"
    )
    # Volume must NOT be in base_name
    assert "_ml" not in attrs["base_name"]
    assert "1ml" not in attrs["base_name"]


def test_volume_variants_share_base_name():
    """Та сама послуга з різним обʼємом → один base_name."""
    a = extract_attributes("Belotero Balance 1 ml")
    b = extract_attributes("Belotero Balance 2 ml")
    assert a["base_name"] == b["base_name"]
    assert a["volume_ml"] == 1.0
    assert b["volume_ml"] == 2.0


# ── Зони ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_zones",
    [
        ("Антицелюлітний масаж 1 зона", 1),
        ("Антицелюлітний масаж 2 зони", 2),
        ("Депіляція 1 зона обличчя", 1),
        ("Анестезія аплікаторна 1 зона", 1),
    ],
)
def test_zones_extracted(name, expected_zones):
    attrs = extract_attributes(name)
    assert attrs["zones"] == expected_zones


def test_zones_variants_collapse():
    a = extract_attributes("Антицелюлітний масаж 1 зона")
    b = extract_attributes("Антицелюлітний масаж 2 зони")
    assert a["base_name"] == b["base_name"]
    assert a["zones"] == 1
    assert b["zones"] == 2


# ── Ампули ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_ampules",
    [
        ("Awapuhi 1 ампула", 1),
        ("Awapuhi 2 ампули", 2),
        ("Awapuhi без ампули", 0),
        ("Awapuhi з ампулою", 1),
    ],
)
def test_ampules_extracted(name, expected_ampules):
    attrs = extract_attributes(name)
    assert attrs["ampules"] == expected_ampules


def test_awapuhi_variants_collapse():
    """Awapuhi 1/2/без — той же base_name."""
    keys = {
        extract_attributes("Awapuhi 1 ампула")["base_name"],
        extract_attributes("Awapuhi 2 ампули")["base_name"],
        extract_attributes("Awapuhi без ампули")["base_name"],
    }
    assert len(keys) == 1, f"Expected 1 base_name, got {keys}"


# ── Тривалість сесії ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_minutes",
    [
        ("Класичний масаж 60 хв", 60),
        ("Спортивний масаж 90 хв", 90),
        ("Масаж 45 min", 45),
        ("Масаж 1 година", 60),
        ("Масаж 2 години", 120),
    ],
)
def test_session_minutes_extracted(name, expected_minutes):
    attrs = extract_attributes(name)
    assert attrs["session_minutes"] == expected_minutes


# ── Опечатки виправляються ─────────────────────────────────────────


def test_antitselyulit_typo_fixes():
    """Друкарські варіанти 'антицылюліт/антицилюліт' → 'антицелюліт'."""
    keys = {
        extract_attributes("Антицелюлітний масаж 1 зона")["base_name"],
        extract_attributes("Антицылюлітний масаж 1 зона")["base_name"],
        extract_attributes("Антицилюлітний масаж 1 зона")["base_name"],
    }
    assert len(keys) == 1, f"Typo variants did not collapse: {keys}"


# ── Add-on суфікси ─────────────────────────────────────────────────


def test_addon_suffix_removed():
    a = normalize_to_canonical_key("Тонування (додатково)")
    b = normalize_to_canonical_key("Тонування")
    assert a == b


# ── Edge cases ─────────────────────────────────────────────────────


def test_empty_name():
    assert normalize_to_canonical_key("") == ""
    assert normalize_to_canonical_key("   ") == ""


def test_extract_attributes_empty():
    attrs = extract_attributes("")
    assert attrs == {
        "base_name": "",
        "brand": None,
        "volume_ml": None,
        "zones": None,
        "session_minutes": None,
        "ampules": None,
    }


# ── Композиційний приклад ──────────────────────────────────────────


def test_full_extraction_complex_name():
    """Реальна послуга з усіма атрибутами."""
    name = "Lebel Absolute Happiness 1 довжина МАЙСТЕР 60 хв"
    attrs = extract_attributes(name)
    assert attrs["brand"] == "lebel"
    assert attrs["session_minutes"] == 60
    # Length / level / brand видалені — лишилось 'absolute happiness'
    assert "absolute" in attrs["base_name"] and "happiness" in attrs["base_name"]
    assert "lebel" not in attrs["base_name"]
    assert "60" not in attrs["base_name"]
    assert "1" not in attrs["base_name"]
