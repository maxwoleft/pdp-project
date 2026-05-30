"""Обчислення canonical_key + структурованих атрибутів послуги.

Канонічний ключ — це нормалізована форма назви послуги, яка
**не залежить** від:
- довжини волосся (1 довжина / Long / Medium / Short)
- рівня майстра (МАЙСТЕР / ТОП / АРТ / БАРБЕР)
- мовних блоків CRM (EN ... / UA ... / RUS ...)
- технічних суфіксів (додатково / additionally)
- бренду (Lebel, Brae, Awapuhi, ...) — виноситься в `service.brand`
- обʼєму препарату (1 мл, 0.55ml) — у `service.volume_ml`
- кількості зон / ампул / тривалості сесії — у відповідні колонки
- порядку слів — токени сортуються детерміновано

Це дозволяє:
- одна "канонічна послуга" → багато конкретних `service.id`
  у різних салонах, у тому числі різні бренди / обʼєми
- один профіль → багато послуг одночасно
- фільтрувати при пошуку: ключ + brand + volume_ml тощо
"""
from __future__ import annotations

import re
import unicodedata

from app.domain.services.canonical_dicts import (
    apply_synonyms,
    apply_typo_fixes,
    get_canonical_brand,
)

# ── Регулярки для видалення варіативних суфіксів ──────────────────

# Довжина волосся / нігтів
_LENGTH_PATTERNS = [
    re.compile(r"\b[\d/]+\s*(?:дов?жин\w*|довжн\w*|длин\w*|length|długoś\w*)\b", re.IGNORECASE),
    re.compile(r"\b(?:дов?жин\w*|довжн\w*|длин\w*|length|długoś\w*)\s*[\d/]+\b", re.IGNORECASE),
    re.compile(r"\(\s*(?:short|medium|long|extra long|tailbone length|junior)\s*\)", re.IGNORECASE),
    re.compile(
        r"\(\s*(?:коротке волосся|середнє волосся|довге волосся|дуже довге волосся|"
        r"короткие волосы|средние волосы|длинные волосы|очень длинные волосы|"
        r"довжина до куприка|длина до копчика)\s*\)",
        re.IGNORECASE,
    ),
]

# Рівень майстра — на кінці назви
_LEVEL_RE = re.compile(
    r"\s*(майстер|мастер|master|топ|top|арт|art|барбер|barber|джуніор|junior|дженіор)\s*$",
    re.IGNORECASE,
)

# Римські цифри / порядкові ("1-а"/"2-а") в кінці або перед роздільниками
_ROMAN_LEVEL_RE = re.compile(r"\s+(?:[IІ]{1,3}|[IІ]V|V|1-а|2-а|3-а|4-а|5-а)(?=\s|$)")

# Знижки (-20%, -30%)
_DISCOUNT_RE = re.compile(r"\s*-\d+%")

# Add-on суфікси
_ADDON_RE = re.compile(
    r"\s*\(\s*(?:додатково|дополнительно|additionally|dodatkowo|add[- ]?on|adicional)\s*\)",
    re.IGNORECASE,
)

# Add-on слова на початку (для фільтрації послуг — використовується ззовні)
_ADDON_WORDS_RE = re.compile(
    r"^(?:додатково|дополнительно|additionally|add[- ]?on)\b",
    re.IGNORECASE,
)

# CRM-формат: "EN ... / UA ... / RUS ..."
_CRM_LANG_RE = re.compile(
    r"(?:^|/)\s*(?:EN|UA|RUS?|PL)\s+([^/]+?)(?=\s*/|$)",
    re.IGNORECASE,
)

# Дозволені символи в slug
_NON_SLUG_RE = re.compile(r"[^a-z0-9а-яёії'іґєў]+", re.IGNORECASE)


# ── Витяг структурованих атрибутів ─────────────────────────────────

# Обʼєм препарату: "1 мл", "0,55мл", "0.6 ml"
_VOLUME_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:мл|ml)\b", re.IGNORECASE)

# Кількість зон: "1 зона", "2 зони"
_ZONES_RE = re.compile(r"\b(\d+)\s*зон[аиу]?\b", re.IGNORECASE)

# Тривалість сесії: "30 хв", "60 min", "1 година"
_SESSION_MIN_RE = re.compile(r"\b(\d+)\s*(?:хв\.?|хвилин|мін\.?|мин\.?|min\.?|m)\b", re.IGNORECASE)
_SESSION_HOUR_RE = re.compile(r"\b(\d+)\s*(?:годин\w*|часов|hour\w*|h)\b", re.IGNORECASE)

# Ампули: "1 ампула / 2 ампули / без ампули / з ампулою"
_AMPULE_NUM_RE = re.compile(r"\b(\d+)\s*ампул\w*\b", re.IGNORECASE)
_AMPULE_NONE_RE = re.compile(r"\bбез\s+ампул\w*\b", re.IGNORECASE)
_AMPULE_WITH_RE = re.compile(r"\bз\s+ампул\w*\b", re.IGNORECASE)


def extract_uk_part_from_crm(name: str) -> str:
    """З трилінгвальної CRM-назви бере UA частину якщо є."""
    parts = {}
    for m in _CRM_LANG_RE.finditer(name):
        full = m.group(0).lstrip("/").strip()
        tag = full.split(None, 1)[0].upper() if full else ""
        text = full[len(tag):].strip().rstrip("/").strip() if tag else ""
        if text and tag in ("UA", "EN", "RUS", "RU", "PL"):
            parts.setdefault(tag, text)
    if "UA" in parts:
        return parts["UA"]
    if parts:
        return next(iter(parts.values()))
    return name


def _extract_volume(text: str) -> tuple[float | None, str]:
    m = _VOLUME_RE.search(text)
    if not m:
        return None, text
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None, text
    cleaned = _VOLUME_RE.sub(" ", text)
    return value, cleaned


def _extract_zones(text: str) -> tuple[int | None, str]:
    m = _ZONES_RE.search(text)
    if not m:
        return None, text
    try:
        value = int(m.group(1))
    except ValueError:
        return None, text
    cleaned = _ZONES_RE.sub(" ", text)
    return value, cleaned


def _extract_session_minutes(text: str) -> tuple[int | None, str]:
    """Тривалість процедури → minutes. 'хв' / 'min' / 'годин'."""
    m_min = _SESSION_MIN_RE.search(text)
    if m_min:
        try:
            value = int(m_min.group(1))
        except ValueError:
            return None, text
        cleaned = _SESSION_MIN_RE.sub(" ", text)
        return value, cleaned

    m_h = _SESSION_HOUR_RE.search(text)
    if m_h:
        try:
            value = int(m_h.group(1)) * 60
        except ValueError:
            return None, text
        cleaned = _SESSION_HOUR_RE.sub(" ", text)
        return value, cleaned
    return None, text


def _extract_ampules(text: str) -> tuple[int | None, str]:
    """Кількість ампул. 'без ампули' → 0; 'з ампулою' → 1; 'N ампул' → N."""
    if _AMPULE_NONE_RE.search(text):
        return 0, _AMPULE_NONE_RE.sub(" ", text)
    m = _AMPULE_NUM_RE.search(text)
    if m:
        try:
            value = int(m.group(1))
        except ValueError:
            return None, text
        return value, _AMPULE_NUM_RE.sub(" ", text)
    if _AMPULE_WITH_RE.search(text):
        return 1, _AMPULE_WITH_RE.sub(" ", text)
    return None, text


# ── Транслітерація + slug ──────────────────────────────────────────

_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d",
    "е": "e", "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i",
    "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ы": "y", "ъ": "", "ь": "", "ю": "yu", "я": "ya", "э": "e", "ё": "yo",
    " ": " ", "'": "", "ʼ": "", "ʹ": "",
}


def _transliterate(text: str) -> str:
    out = []
    for ch in text:
        if ch in _TRANSLIT_MAP:
            out.append(_TRANSLIT_MAP[ch])
        elif ch.isascii():
            out.append(ch)
        else:
            normalized = unicodedata.normalize("NFKD", ch)
            ascii_only = "".join(c for c in normalized if c.isascii())
            out.append(ascii_only if ascii_only else "_")
    return "".join(out)


def _make_slug(text: str, sort_tokens: bool = True) -> str:
    """Текст → транслітерований slug. Опційно сортує токени алфавітно.

    Сортування знімає залежність від порядку слів у CRM (наприклад,
    'медичний педикюр' = 'педикюр медичний'). Без сортування ключ
    залежить від того, як менеджер написав назву.
    """
    text = text.strip().lower()
    text = _transliterate(text)
    text = _NON_SLUG_RE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if sort_tokens:
        tokens = [t for t in text.split("_") if t]
        tokens.sort()
        text = "_".join(tokens)
    return text[:100]


# ── Головні функції ────────────────────────────────────────────────


def extract_attributes(name: str) -> dict:
    """Витягує canonical_key + структуровані атрибути.

    Повертає dict з ключами:
        base_name: str           — canonical_key (для linking з profile)
        brand: str | None        — нормалізований бренд
        volume_ml: float | None  — обʼєм у мл
        zones: int | None        — кількість зон
        session_minutes: int|None — тривалість сесії
        ampules: int | None      — кількість ампул (0 = "без ампули")

    Всі extracted атрибути ВИДАЛЯЮТЬСЯ з base_name, тож canonical_key
    групує всі варіанти за брендом / обʼємом / кількостями разом.
    """
    if not name or not name.strip():
        return {
            "base_name": "",
            "brand": None,
            "volume_ml": None,
            "zones": None,
            "session_minutes": None,
            "ampules": None,
        }

    # 1. Multi-language CRM — беремо UA частину
    text = extract_uk_part_from_crm(name)

    # 2. Lower-case + нормалізація опечаток і синонімів
    text = text.lower()
    text = apply_typo_fixes(text)
    text = apply_synonyms(text)

    # 3. Add-on суфікси — рано, бо часто перед length
    text = _ADDON_RE.sub(" ", text)

    # 4. Витягуємо структуровані атрибути (вони видаляються з тексту)
    brand, text = get_canonical_brand(text)
    volume_ml, text = _extract_volume(text)
    ampules, text = _extract_ampules(text)
    zones, text = _extract_zones(text)
    session_minutes, text = _extract_session_minutes(text)

    # 5. Видаляємо варіативність, що не дає інформативного ключа
    for pat in _LENGTH_PATTERNS:
        text = pat.sub(" ", text)
    text = _DISCOUNT_RE.sub(" ", text)

    # 6. Рівень майстра і римські цифри — у циклі (буває "топ майстер")
    while True:
        new = _LEVEL_RE.sub("", text).strip()
        if new == text:
            break
        text = new
    while True:
        new = _ROMAN_LEVEL_RE.sub("", text).strip()
        if new == text:
            break
        text = new

    # 7. Slug + token sort
    base_name = _make_slug(text, sort_tokens=True)

    return {
        "base_name": base_name,
        "brand": brand,
        "volume_ml": volume_ml,
        "zones": zones,
        "session_minutes": session_minutes,
        "ampules": ampules,
    }


def normalize_to_canonical_key(name: str) -> str:
    """Backward-compatible API: повертає тільки canonical_key (base_name).

    Існуючий код, що очікує тільки string, продовжує працювати.
    Для отримання структурованих атрибутів — використовуй extract_attributes().
    """
    return extract_attributes(name)["base_name"]


def make_canonical_key(attrs: dict) -> str | None:
    """Збирає canonical_key для запису в БД з результату extract_attributes.

    Якщо base_name порожній (типу "Айкун 60 хв" — вся семантика витягнута
    в атрибути), використовуємо бренд як fallback, щоб послуга мала валідний
    ключ для linking з профілем.
    """
    base = attrs.get("base_name") or ""
    if base:
        return base
    brand = attrs.get("brand")
    if brand:
        # Бренд може бути багатослівним ("brae power dose") — slug-ify
        return _make_slug(brand, sort_tokens=False) or None
    return None
