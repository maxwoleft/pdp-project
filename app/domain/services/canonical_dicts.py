"""Словники нормалізації для canonical_key.

Логіка `canonical_key.py` залишається в одному файлі, а перелік
конкретних брендів / опечаток / синонімів — тут, щоб додавати
нові варіанти без редагування алгоритму.

Усі ключі словників — у lowercase. Порівняння робиться вже після
.lower() у вхідній назві.
"""
from __future__ import annotations

# ── Опечатки / варіанти написання ─────────────────────────────────
# Застосовується ПЕРЕД транслітерацією, на оригінальному тексті в lower.
# Ключ = регекс (re.IGNORECASE), значення = canonical replacement.
# Порядок важливий: довші патерни мають бути перед коротшими.
TYPO_FIXES: list[tuple[str, str]] = [
    # Schwarzkopf — у CRM 40 послуг з опечаткою
    (r"\bschwarskopf\b", "schwarzkopf"),
    # La Biosthetique — варіанти написання → один канонічний з пробілом
    (r"\blabiosthetique\b", "la biosthetique"),
    (r"\b(?<!la )biosthetique\b", "la biosthetique"),
    # антицелюліт
    (r"\bантицылюліт", "антицелюліт"),
    (r"\bантицилюліт", "антицелюліт"),
    (r"\bантитсилюліт", "антицелюліт"),
    (r"\bантитселюліт", "антицелюліт"),
    # Дефіс vs пробіл — уніфікуємо до пробілу
    (r"\bблонд-миття\b", "блонд миття"),
    # Apostrophes — три варіанти Unicode → один ASCII
    ("ʼ", "'"),
    ("’", "'"),
    ("`", "'"),
]

# ── Synonyms (нормалізація еквівалентних термінів) ────────────────
# Eng/UA/RU варіанти однієї послуги. Приводимо до канонічного UA.
SYNONYMS: list[tuple[str, str]] = [
    # Гель-лак
    (r"\bшеллак\b", "гель-лак"),
    (r"\bшелак\b", "гель-лак"),
    (r"\bshellac\b", "гель-лак"),
    (r"\bgel polish\b", "гель-лак"),
    # Air Touch
    (r"\bейртач\b", "airtouch"),
    (r"\bаіртач\b", "airtouch"),
    (r"\bаіртач\b", "airtouch"),
    (r"\bair touch\b", "airtouch"),
    (r"\bair-touch\b", "airtouch"),
    # Бровий контекст
    (r"\bbrow\b", "брови"),
    (r"\bbrwi\b", "брови"),
    # Манікюр/педикюр RU
    (r"\bманикюр\b", "манікюр"),
    (r"\bпедикюр\b", "педикюр"),  # ru→uk same spelling
    # Стрижка
    (r"\bhair cut\b", "стрижка"),
    (r"\bhaircut\b", "стрижка"),
]

# ── Бренди ────────────────────────────────────────────────────────
# Алгоритм проходиться цим списком і ВИТЯГУЄ бренд у окрему колонку,
# видаляючи з назви. Виявлення йде на вже нормалізованому тексті
# (після TYPO_FIXES + SYNONYMS, у lowercase).
#
# Ключ списку = canonical brand name (зберігаємо в БД).
# Значення = список регексів, що шукаємо в назві.
#
# Порядок важливий: специфічніші серії (наприклад "tokio inkarami")
# мають бути перед загальним брендом ("inkarami" / "lebel").
BRANDS: list[tuple[str, list[str]]] = [
    # Багатослівні серії — перші
    ("brae bond angel", [r"\bbrae\s+bond\s+angel\b", r"\bbond\s+angel\b"]),
    ("brae power dose", [r"\bbrae\s+power\s+dose\b", r"\bpower\s+dose\b"]),
    ("tokio inkarami", [r"\btokio\s+inkarami\b"]),
    ("dr.sorbie", [r"\bdr\.?\s*sorbie\b"]),
    ("la biosthetique", [r"\bla\s+biosthetique\b"]),
    ("biologique recherche", [r"\bbiologique\s+recherche\b"]),
    ("hadat cosmetics", [r"\bhadat\s+cosmetics\b", r"\bhadat\b"]),
    # Однослівні бренди (за алфавітом)
    ("balmain", [r"\bbalmain\b"]),
    ("brae", [r"\bbrae\b"]),
    ("cronna", [r"\bcronna\b"]),
    ("davines", [r"\bdavines\b"]),
    ("dmk", [r"\bdmk\b"]),
    ("forlled", [r"\bforlle'?d\b", r"\bforlled\b"]),
    ("ibx", [r"\bibx\b"]),
    ("inkarami", [r"\binkarami\b"]),
    ("inoa", [r"\binoa\b"]),
    ("keune", [r"\bkeune\b"]),
    ("lebel", [r"\blebel\b"]),
    ("loreal", [r"\bl'?oreal\b", r"\bлореаль\b", r"\bлореал\b"]),
    ("milbon", [r"\bmilbon\b"]),
    ("nashi", [r"\bnashi\b"]),
    ("olaplex", [r"\bolaplex\b"]),
    ("oribe", [r"\boribe\b"]),
    ("redken", [r"\bredken\b"]),
    ("revival", [r"\brevival\b"]),
    ("schwarzkopf", [r"\bschwarzkopf\b"]),
    ("sorbie", [r"\bsorbie\b"]),
    ("wella", [r"\bwella\b"]),
    # Філлер-бренди (естетична медицина)
    ("juvederm", [r"\bjuvederm\b"]),
    ("belotero", [r"\bbelotero\b"]),
    ("stylage", [r"\bstylage\b"]),
    ("teosyal", [r"\bteosyal\b"]),
    ("radiesse", [r"\bradiesse\b"]),
    ("aliaxin", [r"\baliaxin\b"]),
    ("dermaheal", [r"\bdermaheal\b"]),
    # Apparat
    ("icoone", [r"\bicoone\b", r"\bайкун\b"]),
    ("aquapure", [r"\baquapure\b", r"\baquaPure\b"]),
]


def get_canonical_brand(text_lower: str) -> tuple[str | None, str]:
    """Знаходить бренд у назві. Повертає (brand_name, text_without_brand).

    text_lower має бути вже у lower-case.
    """
    import re

    for brand, patterns in BRANDS:
        for pat in patterns:
            if re.search(pat, text_lower, flags=re.IGNORECASE):
                cleaned = re.sub(pat, " ", text_lower, flags=re.IGNORECASE)
                return brand, cleaned
    return None, text_lower


def apply_typo_fixes(text: str) -> str:
    """Виправляє опечатки. text у lowercase."""
    import re

    for pattern, replacement in TYPO_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def apply_synonyms(text: str) -> str:
    """Уніфікує синоніми. text у lowercase."""
    import re

    for pattern, replacement in SYNONYMS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
