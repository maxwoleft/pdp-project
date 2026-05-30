"""Селективний підвантажувач прикладних діалогів.

Дивиться на текст останнього user-повідомлення, визначає 1-2 релевантні
категорії і повертає їх вміст для system prompt.

Обмеження: до MAX_EXAMPLES_PER_CATEGORY прикладів з кожної категорії —
щоб не роздувати контекст-вікно (інакше деякі категорії дають по 60+ діалогів).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

MAX_EXAMPLES_PER_CATEGORY = 8

EXAMPLES_DIR = Path(__file__).parent / "instructions" / "dialogue_examples"

# Ті самі ключові слова що і в build_dialogue_examples.py — інакше класифікація
# на runtime буде не збігатись з тим, що було при підготовці файлів.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "nails": [
        "манікюр", "маникюр", "manicure", "педикюр", "pedicure",
        "гель-лак", "гель лак", "gel", "нігт", "ногт", "nail",
        "покритт", "покрыти",
    ],
    "hair_cut": [
        "стрижк", "стрижку", "haircut", "підрізати", "подрезать",
        "кінчик", "кончик", "укладк", "blow", "сушк",
    ],
    "hair_color": [
        "фарб", "красит", "колор", "color", "tint", "тонуван", "тонир",
        "корен", "корн", "airtouch", "airtach", "балаяж",
        "мелірув", "мелиров", "highlight", "осветлен", "висвітлен",
    ],
    "hair_care": [
        "догляд для волосся", "уход за волосами", "ботокс", "botox",
        "кератин", "keratin", "реконструкц", "відновлен волосс",
        "восстановлен волос", "lebel",
    ],
    "brows_lashes": [
        "брів", "бров", "brow", "ламінув", "ламинир",
        "вії", "ресн", "lash", "перманент", "permanent",
    ],
    "face_body": [
        "масаж", "массаж", "massage", "косметолог", "robolex", "роболекс",
        "icoone", "ендосфер", "stratosphere", "чистк лиц", "чистка лиц",
    ],
    "cancel_reschedule": [
        "скасув", "відмін", "отмен", "cancel",
        "перенест", "перенос", "reschedule", "не зможу", "не смогу",
    ],
    "price_questions": [
        "ціна", "цена", "вартість", "стоимость", "скільки коштує",
        "сколько стоит", "почем", "price", "дорог", "знижк", "скидк",
    ],
    "complaints_difficult": [
        "не сподобал", "не понравил", "погано", "плохо", "повертаю",
        "скарга", "жалоба", "complaint", "проблем",
    ],
}


class DialogueMatcher:
    """Кешує файли в пам'яті при першому виклику."""

    def __init__(self, examples_dir: Path = EXAMPLES_DIR) -> None:
        self._dir = examples_dir
        self._cache: dict[str, str] = {}

    def _load(self, category: str) -> str:
        if category in self._cache:
            return self._cache[category]
        path = self._dir / f"{category}.md"
        if not path.exists():
            self._cache[category] = ""
            return ""
        text = path.read_text(encoding="utf-8")
        text = self._truncate(text, MAX_EXAMPLES_PER_CATEGORY)
        self._cache[category] = text
        return text

    @staticmethod
    def _truncate(text: str, max_examples: int) -> str:
        """Обрізає до перших N прикладів (заголовок + перші N блоків '## Приклад')."""
        # Розбиваємо на header + блоки прикладів
        parts = re.split(r"(?m)^## Приклад \d+", text)
        if len(parts) <= 1:
            return text
        header = parts[0]
        examples = parts[1 : 1 + max_examples]
        rebuilt = header
        for i, body in enumerate(examples, start=1):
            rebuilt += f"## Приклад {i}{body}"
        return rebuilt

    def pick_categories(self, text: str, max_categories: int = 2) -> list[str]:
        """Повертає до N релевантних категорій, відсортованих за кількістю збігів."""
        if not text:
            return []
        text_lower = text.lower()
        counter: Counter[str] = Counter()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    counter[cat] += 1
        if not counter:
            return []
        return [cat for cat, _ in counter.most_common(max_categories)]

    def build_context(self, text: str, max_categories: int = 2) -> str:
        cats = self.pick_categories(text, max_categories=max_categories)
        if not cats:
            return ""
        parts: list[str] = []
        for cat in cats:
            content = self._load(cat).strip()
            if content:
                parts.append(content)
        if not parts:
            return ""
        header = (
            "# DIALOGUE EXAMPLES (relevant to current request)\n\n"
            "Це РЕАЛЬНІ переписки наших адміністраторів з клієнтами. "
            "Орієнтуйся на ТОН, СТРУКТУРУ і КОНКРЕТНІ ФРАЗИ — це еталон того, "
            "як саме треба відповідати у схожих ситуаціях. Не копіюй дослівно — "
            "адаптуй під поточний контекст, але зберігай стиль.\n\n---\n\n"
        )
        return header + "\n\n---\n\n".join(parts)
