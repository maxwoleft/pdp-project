"""Парсить examples/final_dialogues_merged.txt і розкладає діалоги по категоріях.

Кожна категорія → окремий .md файл у app/agents/instructions/dialogue_examples/.
Запуск:
    python -m scripts.build_dialogue_examples
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "examples" / "final_dialogues_merged.txt"
OUT_DIR = ROOT / "app" / "agents" / "instructions" / "dialogue_examples"

DIALOG_HEADER = re.compile(r"^Діалог\s+\d+\s*$")

# Категорії з ключовими словами (lowercase substring match по всьому тексту діалогу).
# Якщо діалог матчиться в КІЛЬКА — він потрапляє в усі.
CATEGORIES: dict[str, dict] = {
    "nails": {
        "title": "Манікюр / Педикюр / Нігті",
        "keywords": [
            "манікюр", "маникюр", "manicure", "педикюр", "pedicure",
            "гель-лак", "гель лак", "gel", "нігт", "ногт", "nail",
            "покритт", "покрыти", "spa", "спа",
        ],
    },
    "hair_cut": {
        "title": "Стрижки",
        "keywords": [
            "стрижк", "стрижку", "haircut", "підрізати", "подрезать",
            "кінчик", "кончик", "укладк", "blow", "сушк",
        ],
    },
    "hair_color": {
        "title": "Фарбування / Колір волосся",
        "keywords": [
            "фарб", "красит", "колор", "color", "tint", "тонуван", "тонир",
            "корен", "корн", "airtouch", "airtach", "балаяж", "балаяж",
            "мелірув", "мелиров", "highlight", "осветлен", "висвітлен",
        ],
    },
    "hair_care": {
        "title": "Догляд за волоссям",
        "keywords": [
            "догляд", "уход", "treatment", "ботокс", "botox", "кератин", "keratin",
            "реконструкц", "відновлен", "восстановлен", "маск", "lebel",
        ],
    },
    "brows_lashes": {
        "title": "Брови / Вії",
        "keywords": [
            "брів", "бров", "brow", "оформлен", "ламінув", "ламинир",
            "вії", "ресн", "lash", "перманент", "permanent",
        ],
    },
    "face_body": {
        "title": "Косметологія / Масаж / Апаратні процедури",
        "keywords": [
            "масаж", "массаж", "massage", "косметолог", "robolex", "роболекс",
            "icoone", "ендосфер", "stratosphere", "чистк", "чистка",
            "обличч", "лиц", "face", "тіло", "тело", "body",
        ],
    },
    "cancel_reschedule": {
        "title": "Скасування та перенесення",
        "keywords": [
            "скасув", "відмін", "отмен", "cancel",
            "перенест", "перенос", "reschedule", "змінити час", "змінити дат",
            "не зможу", "не смогу", "не вийде", "не получится",
        ],
    },
    "price_questions": {
        "title": "Питання про ціну",
        "keywords": [
            "ціна", "цена", "вартість", "стоимость", "скільки коштує", "сколько стоит",
            "почем", "price", "дорог", "дешев", "знижк", "скидк", "discount",
        ],
    },
    "complaints_difficult": {
        "title": "Складні ситуації / претензії",
        "keywords": [
            "не сподобал", "не понравил", "погано", "плохо", "повертаю", "возвращ",
            "скарга", "жалоба", "complaint", "проблем", "не задовольня",
            "обман", "неякісн", "некачественн",
        ],
    },
}

INTRO = """# DIALOGUE EXAMPLES — {title}

Реальні приклади переписок з клієнтами. Орієнтуйся на ТОН і СТРУКТУРУ цих відповідей,
коли клієнт ставить схожий запит. Не копіюй дослівно — адаптуй під поточний контекст,
але зберігай:
- стиль фраз («Так, звісно.», «Можемо запросити вас...», «Сформували для вас запис...»)
- довжину повідомлень (1-2 короткі речення)
- порядок кроків
- природність і теплоту

---

"""


def split_dialogues(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\ufeff", "").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if DIALOG_HEADER.match(line.strip()):
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    # очищаємо: викидаємо порожні діалоги, тримінг
    out: list[str] = []
    for b in blocks:
        body = "\n".join(b).strip()
        if body:
            out.append(body)
    return out


def categorise(dialogue: str) -> set[str]:
    text = dialogue.lower()
    matched: set[str] = set()
    for cat, info in CATEGORIES.items():
        for kw in info["keywords"]:
            if kw in text:
                matched.add(cat)
                break
    return matched


def write_category(cat: str, title: str, dialogues: list[str]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{cat}.md"
    parts = [INTRO.format(title=title)]
    for i, d in enumerate(dialogues, start=1):
        parts.append(f"## Приклад {i}\n\n```\n{d}\n```\n")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    dialogues = split_dialogues(text)
    print(f"Parsed dialogues: {len(dialogues)}")

    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    uncat: list[str] = []
    for d in dialogues:
        cats = categorise(d)
        if not cats:
            uncat.append(d)
            continue
        for c in cats:
            by_cat[c].append(d)

    for cat, info in CATEGORIES.items():
        if not by_cat[cat]:
            continue
        path = write_category(cat, info["title"], by_cat[cat])
        print(f"  {cat:22s} → {len(by_cat[cat]):3d} dialogues  →  {path.relative_to(ROOT)}")

    if uncat:
        path = write_category("misc", "Інше / Різне", uncat)
        print(f"  {'misc':22s} → {len(uncat):3d} dialogues  →  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
