"""Переклад назв послуг.

Два режими:
1. Парсинг трилінгвальних назв з CRM (GB/PL): "EN Root... / UA Фарб... / RUS Окраш..."
2. Автопереклад моноязичних назв (UA) через GPT-5 nano батчами.

Використовується при sync_from_crm для заповнення name_uk / name_ru / name_en / name_pl.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings

log = logging.getLogger(__name__)

# ── Парсер мовних міток CRM ────────────────────────────────────────
_LANG_TAG_RE = re.compile(
    r"(?:^|/)\s*(?P<lang>EN|UA|RUS?|PL)\s+(?P<text>[^/]+?)(?=\s*/|$)",
    re.IGNORECASE,
)

LANG_MAP = {"EN": "en", "UA": "uk", "RUS": "ru", "RU": "ru", "PL": "pl"}
ALL_LANGS = ("uk", "ru", "en", "pl")

TRANSLATE_BATCH_SIZE = 50  # назв за один GPT виклик


def parse_multilingual_name(name: str) -> dict[str, str] | None:
    """Парсить 'EN Root... / UA Фарб... / RUS Окраш...' → {"en": "Root...", "uk": "Фарб..."}.

    ВАЖЛИВО: беремо ТІЛЬКИ EN і UA з CRM-назв (вони авторські).
    RUS часто неточний у CRM (напр. «Осветление» замість «Окрашивание»),
    тому RU і PL завжди перекладаємо через GPT.

    Повертає None якщо назва не містить мовних міток (моноязична).
    """
    found: dict[str, str] = {}
    for m in _LANG_TAG_RE.finditer(name):
        tag = m.group("lang").upper()
        lang = LANG_MAP.get(tag)
        # Беремо тільки EN і UA — решту перекладемо якісно через LLM
        if lang in ("en", "uk") and lang not in found:
            text = m.group("text").strip().rstrip("/").strip()
            if text:
                found[lang] = text
    return found if len(found) >= 2 else None


def detect_source_lang(name: str) -> str:
    """Евристика для моноязичних назв: визначити мову на основі символів."""
    has_cyrillic = bool(re.search(r"[а-яіїєґ]", name, re.IGNORECASE))
    has_polish = bool(re.search(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", name))
    has_ukrainian = bool(re.search(r"[іїєґІЇЄҐ]", name))

    if has_polish:
        return "pl"
    if has_ukrainian:
        return "uk"
    if has_cyrillic:
        return "ru"
    return "en"


class ServiceTranslator:
    """Перекладає назви послуг через GPT-5 nano батчами."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = "gpt-5"

    async def translate_batch(
        self,
        names: list[str],
        source_lang: str,
        target_langs: list[str],
    ) -> list[dict[str, str]]:
        """Перекладає список назв послуг на цільові мови.

        Повертає list[{"uk": "...", "ru": "...", "en": "...", "pl": "..."}],
        де source_lang теж присутній (copy as-is).
        """
        if not names:
            return []

        lang_labels = {"uk": "Ukrainian", "ru": "Russian", "en": "English", "pl": "Polish"}
        target_labels = [lang_labels[l] for l in target_langs]

        results: list[dict[str, str]] = []
        for i in range(0, len(names), TRANSLATE_BATCH_SIZE):
            chunk = names[i : i + TRANSLATE_BATCH_SIZE]
            numbered = "\n".join(f"{j+1}. {n}" for j, n in enumerate(chunk))

            fields_hint = ", ".join(f'"{l}": "<translation>"' for l in target_langs)
            prompt = (
                f"Translate these beauty salon service names from {lang_labels[source_lang]} "
                f"to {', '.join(target_labels)}.\n"
                f'Return JSON object with key "items" containing array. Each element: '
                f'{{"i": <number>, {fields_hint}}}.\n'
                f"Keep brand names (Airtouch, K18, Lebel, REDKEN, etc.) as-is.\n"
                f"Keep size indicators (Short, Medium, Long, etc.) in the target language.\n\n"
                f"{numbered}"
            )

            MAX_RETRIES = 3
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        max_completion_tokens=4000,
                        response_format={"type": "json_object"},
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    arr = self._extract_json_array(text)
                    if arr is None:
                        if attempt < MAX_RETRIES - 1:
                            log.warning("Translation: retry %d/%d — no JSON in response", attempt + 1, MAX_RETRIES)
                            await asyncio.sleep(1)
                            continue
                        log.error("Translation: all %d retries failed for batch %d, filling originals", MAX_RETRIES, i)
                        for name in chunk:
                            results.append({source_lang: name})
                        break

                    by_idx: dict[int, dict[str, str]] = {}
                    for item in arr:
                        idx = item.get("i", 0)
                        by_idx[idx] = {l: item.get(l, "") for l in target_langs}

                    for j, name in enumerate(chunk):
                        trans = by_idx.get(j + 1, {})
                        trans[source_lang] = name
                        results.append(trans)
                    break  # success

                except Exception as exc:
                    if attempt < MAX_RETRIES - 1:
                        log.warning("Translation batch %d attempt %d failed: %s", i, attempt + 1, exc)
                        await asyncio.sleep(2)
                        continue
                    log.error("Translation batch %d failed after %d retries: %s", i, MAX_RETRIES, exc)
                    for name in chunk:
                        results.append({source_lang: name})

        return results

    @staticmethod
    def _extract_json_array(text: str) -> list[dict] | None:
        """Стійкий парсер JSON з GPT-відповіді.

        Підтримує:
        - {"items": [...]}  (response_format=json_object)
        - [{"i":1,...}]
        - ```json\n[...]\n```
        - "Here are:\n[...]"
        """
        if not text:
            return None
        text = text.replace("```json", "").replace("```", "").strip()

        # 1. JSON object з ключем items/translations/data
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for key in ("items", "translations", "data", "results"):
                    if isinstance(obj.get(key), list):
                        return obj[key]
                for v in obj.values():
                    if isinstance(v, list):
                        return v
            if isinstance(obj, list):
                return obj
        except json.JSONDecodeError:
            pass

        # 2. Масив як підрядок
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                arr = json.loads(text[start : end + 1])
                if isinstance(arr, list):
                    return arr
            except json.JSONDecodeError:
                pass
        return None

    async def fill_translations(
        self, name: str, existing: dict[str, str | None] | None = None
    ) -> dict[str, str]:
        """Для однієї послуги: визначає мову, парсить або перекладає, заповнює пропуски."""
        # 1. Спробуємо парсити трилінгвальну назву CRM
        parsed = parse_multilingual_name(name)
        if parsed:
            # Доповнюємо відсутні мови перекладом
            missing = [l for l in ALL_LANGS if l not in parsed]
            if missing and parsed:
                source = next(iter(parsed))
                batch = await self.translate_batch(
                    [parsed[source]], source_lang=source, target_langs=missing
                )
                if batch:
                    for l in missing:
                        parsed[l] = batch[0].get(l, "")
            return parsed

        # 2. Моноязична — визначаємо мову + перекладаємо
        src = detect_source_lang(name)
        target = [l for l in ALL_LANGS if l != src]
        batch = await self.translate_batch([name], source_lang=src, target_langs=target)
        result = batch[0] if batch else {src: name}
        result[src] = name
        return result
