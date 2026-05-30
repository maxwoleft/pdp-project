# file: beauty_bot.py
# Python 3.10+
# pip install python-telegram-bot==21.4 python-dotenv==1.0.1
# .env:
#   TELEGRAM_BOT_TOKEN=123456789:ABC...your_token_here
#   DATABASE_URL=mysql+pymysql://user:pass@host/dbname?charset=utf8mb4
#   (опційно) CATEGORIES_PATH, SERVICES_PATH, EMPLOYEES_PATH, FREETIME_PATH, GROUPS_PATH, ALIASES_PATH, ALIASES_PATH

import os
import json
import logging
import asyncio
import time
import math
import base64
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple, Iterable

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from openai_client import (
    openai_reply_with_usage,
    transcribe_audio,
    extract_conversation_memory,
    embed_texts,
)  # gpt-5-mini + gpt-4-mini-transcribe
from appointment_tools import APPOINTMENT_TOOLS, execute_tool, clear_lookup_cache
import random
import re
import unicodedata

# ===== DB INTEGRATION =====
# ці імпорти очікують твій storage.py з попереднього кроку
from storage import (
    init_db,
    save_user_if_needed,
    save_message_log,
    save_booking,
    fetch_recent_messages,
    get_conversation_memory,
    upsert_conversation_memory,
    upsert_beauty_client,
    get_beauty_client,
    is_ai_enabled,
)
from client_profile_updater import extract_profile_from_context, merge_profile_data
from salons.create_appointments import create_appointment, CRMError
import salons.create_appointments
from salons.config import SALONS
from appointment_helper import (
    find_client_by_contact,
    get_client_appointments,
    cancel_client_appointment,
    reschedule_client_appointment,
)
from session_manager import SessionManager, MessageEvent
manager = SessionManager(wait_timeout=15)

async def process_ai_reply(context, user_id: int, combined_text: str, photos: list = None):
    """Обробка AI відповіді для буферизованих повідомлень"""
    try:
        # Тут буде логіка з handle_free_question для AI обробки
        # Поки що просто відправляємо підтвердження
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
        # TODO: викликати AI обробку
        log.info(f"Processing AI reply for user {user_id}: {combined_text[:100]}")
    except Exception as e:
        log.error(f"Error processing AI reply: {e}")

async def tick_timers(context):
    replies = manager.run_timers()
    for r in replies:
        await context.bot.send_message(chat_id=int(r.user_id), text=r.text)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("beauty-bot")

# Зменшуємо рівень логування для apscheduler
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# Увімкнути детальне логування для create_appointments
logging.getLogger('salons.create_appointments').setLevel(logging.INFO)

# ------------------------ STATES ------------------------
LANGUAGE, SALON, GROUP, HAIR_LENGTH, SERVICE_PICK, MASTER_MODE, MASTER_PICK, MONTH, DAY, TIME_PICK = range(10)
# додаємо стани для питань/підтвердження
FREE_QUESTION, INTENT_DECISION, AUTOCONFIRM = range(10, 13)

# ------------------------ I18N TEXTS ---------------------
LANG_CODES = {"English": "en", "Українська": "uk", "Русский": "ru"}

I18N = {
    "en": {
        "ASK_LANG": "Please choose your language:",
        "ASK_SALON": "Choose a salon location:",
        "ASK_GROUP": "Pick a service direction:",
        "ASK_HAIR_LENGTH": "Select your hair length:",
        "ASK_SERVICE_PICK": "Choose a service from the list:",
        "LEVEL_HINT": "Legend: ⭐ TOP, 🎨 ART, 👩‍🎨 Master",
        "ASK_MASTER_MODE": "Would you like to book a specific artist and view their schedule, or pick a visit date and see who’s available that day?",
        "ASK_MONTH": "Choose a month with available slots:",
        "ASK_DAY": "Choose a day (showing only days with free time):",
        "ASK_MASTER_PICK": "Choose a specialist:",
        "ASK_TIME_PICK": "Choose a time:",
        "CONFIRM": "✅ Booking: *{service}* → *{master}* on *{date} {time}*. We'll contact you to confirm.",
        "NO_MATCHES": "No services found in this direction. Try another.",
        "NO_EMPLOYEES": "No specialists for this service.",
        "NO_SLOTS": "No available dates/times. Try another month or change parameters.",
        "CANCEL_TEXT": "Booking cancelled. Use /start to begin again.",
        "CHOOSE_FROM_KB": "Please choose an option from the keyboard.",
        "NOT_RECOGNIZED": "I didn't recognize that. Please choose from the options.",
        "ANY_MASTER_BTN": "By date (available)",
        "SPECIFIC_MASTER_BTN": "Specific artist (schedule)",
        "PICK_MONTH_AGAIN": "I didn't catch the month. Please select from the list.",
        "PICK_DAY_AGAIN": "This day is not available. Pick a different day.",
        "PICK_TIME_AGAIN": "This time is no longer available. Pick another.",
        "FORMAT_PICK_PAIR": "Please choose an option in the format 'Artist — time'.",
        "NAV_PREV": "◀ Prev",
        "NAV_NEXT": "Next ▶",
        "SERVICES_PAGE": "Page {page}/{pages}",
    },
    "uk": {
        "ASK_LANG": "Оберіть мову спілкування:",
        "ASK_SALON": "Оберіть салон:",
        "ASK_GROUP": "Оберіть напрям послуг:",
        "ASK_HAIR_LENGTH": "Оберіть довжину волосся:",
        "ASK_SERVICE_PICK": "Оберіть послугу зі списку:",
        "LEVEL_HINT": "Пояснення: ⭐ ТОП, 🎨 АРТ, 👩‍🎨 Майстер",
        "ASK_MASTER_MODE": "Хочете запис до конкретного майстра та подивитись його графік для запису чи обираємо за конкретною датою візиту до тих майстрів, хто буде доступний в цей день?",
        "ASK_MONTH": "Оберіть місяць з доступними слотами:",
        "ASK_DAY": "Оберіть день (показую лише дні з вільним часом):",
        "ASK_MASTER_PICK": "Оберіть майстра:",
        "ASK_TIME_PICK": "Оберіть час:",
        "CONFIRM": "✅ Запис: *{service}* → *{master}* на *{date} {time}*. Звʼяжемось для підтвердження.",
        "NO_MATCHES": "Наразі немає послуг у цьому напрямі. Оберіть інший.",
        "NO_EMPLOYEES": "Немає майстрів під цю послугу.",
        "NO_SLOTS": "Немає доступних дат/часів. Оберіть інший місяць або змініть параметри.",
        "CANCEL_TEXT": "Запис скасовано. Скористайтесь /start, щоб почати знову.",
        "CHOOSE_FROM_KB": "Будь ласка, оберіть з клавіатури.",
        "NOT_RECOGNIZED": "Не розпізнав. Будь ласка, оберіть із варіантів.",
        "ANY_MASTER_BTN": "За датою (хто вільний)",
        "SPECIFIC_MASTER_BTN": "Конкретний майстер (графік)",
        "PICK_MONTH_AGAIN": "Не впізнав місяць. Оберіть зі списку.",
        "PICK_DAY_AGAIN": "Цей день недоступний. Оберіть інший.",
        "PICK_TIME_AGAIN": "Цей час вже недоступний. Оберіть інший.",
        "FORMAT_PICK_PAIR": "Оберіть варіант у форматі 'Майстер — час'.",
        "NAV_PREV": "◀ Назад",
        "NAV_NEXT": "Далі ▶",
        "SERVICES_PAGE": "Сторінка {page}/{pages}",
    },
    "ru": {
        "ASK_LANG": "Выберите язык общения:",
        "ASK_SALON": "Выберите салон:",
        "ASK_GROUP": "Выберите направление услуг:",
        "ASK_HAIR_LENGTH": "Выберите длину волос:",
        "ASK_SERVICE_PICK": "Выберите услугу из списка:",
        "LEVEL_HINT": "Подсказка: ⭐ ТОП, 🎨 АРТ, 👩‍🎨 Мастер",
        "ASK_MASTER_MODE": "Хотите записаться к конкретному мастеру и посмотреть его график, или выбрать дату визита и записаться к тем, кто будет доступен в этот день?",
        "ASK_MONTH": "Выберите месяц с доступными слотами:",
        "ASK_DAY": "Выберите день (показываю только дни со свободным временем):",
        "ASK_MASTER_PICK": "Выберите мастера:",
        "ASK_TIME_PICK": "Выберите время:",
        "CONFIRM": "✅ Запись: *{service}* → *{master}* на *{date} {time}*. Мы свяжемся для подтверждения.",
        "NO_MATCHES": "Сейчас нет услуг в этом направлении. Выберите другое.",
        "NO_EMPLOYEES": "Нет мастеров под эту услугу.",
        "NO_SLOTS": "Нет доступных дат/времени. Попробуйте другой месяц или измените параметры.",
        "CANCEL_TEXT": "Запись отменена. Используйте /start, чтобы начать заново.",
        "CHOOSE_FROM_KB": "Пожалуйста, выберите с клавиатуры.",
        "NOT_RECOGNIZED": "Не распознал. Пожалуйста, выберите из вариантов.",
        "ANY_MASTER_BTN": "По дате (кто свободен)",
        "SPECIFIC_MASTER_BTN": "Конкретный мастер (график)",
        "PICK_MONTH_AGAIN": "Не распознал месяц. Выберите из списка.",
        "PICK_DAY_AGAIN": "Этот день недоступен. Выберите другой.",
        "PICK_TIME_AGAIN": "Это время уже недоступно. Выберите другое.",
        "FORMAT_PICK_PAIR": "Выберите вариант в формате 'Мастер — время'.",
        "NAV_PREV": "◀ Назад",
        "NAV_NEXT": "Далее ▶",
        "SERVICES_PAGE": "Стр. {page}/{pages}",
    },
}

def T(ctx: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    lang = ctx.user_data.get("lang") or "uk"
    return I18N.get(lang, I18N["uk"]).get(key, key)

def kb(rows: List[List[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def normalize(s: str) -> str:
    return (s or "").strip().lower()


def _tokenize_words(text: str) -> List[str]:
    return re.findall(r"[a-zA-Zа-яА-ЯёЁіїІЇєЄґҐ]+", (text or "").lower())


def _is_examples_request(text: str) -> bool:
    tokens = _tokenize_words(text)
    if not tokens:
        return False

    token_set = set(tokens)
    joined = " ".join(tokens)

    single_triggers = {"портфоліо", "портфолио", "portfolio"}
    if token_set & single_triggers:
        return True

    phrase_triggers = [
        "приклади робіт",
        "приклади роботи",
        "приклад робіт",
        "фото робіт",
        "фото роботи",
        "photo of work",
        "photos of work",
        "photo of your work",
        "photos of your work",
        "examples of work",
        "example of work",
        "work examples",
        "work photos",
        "work photo",
        "примеры работ",
        "пример работ",
        "примеры ваших работ",
        "фото работ",
        "фото ваших работ",
    ]
    for phrase in phrase_triggers:
        if phrase in joined:
            return True

    combo_triggers = [
        {"приклад", "робіт"},
        {"приклади", "робіт"},
        {"приклад", "роботи"},
        {"приклади", "роботи"},
        {"пример", "работ"},
        {"примеры", "работ"},
        {"пример", "работы"},
        {"примеры", "работы"},
        {"пример", "ваших", "работ"},
        {"фото", "робіт"},
        {"фото", "роботи"},
        {"фото", "работ"},
    ]
    for combo in combo_triggers:
        if combo <= token_set:
            return True

    return False

# ------------------------ TEXT NORMALIZATION UTILITIES -----
_CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "h",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "є": "ie",
    "ж": "zh",
    "з": "z",
    "и": "y",
    "і": "i",
    "ї": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ь": "",
    "ъ": "",
    "ы": "y",
    "э": "e",
    "ю": "iu",
    "я": "ia",
    "ё": "e",
}

MATCH_STOPWORDS = {
    "road",
    "street",
    "st",
    "rd",
    "old",
    "new",
    "the",
    "salon",
    "studio",
    "beauty",
    "avenue",
}


def transliterate_cyr_to_lat(text: str) -> str:
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def tokenize_for_match(text: str) -> set[str]:
    if not text:
        return set()
    lowered = text.lower()
    transliterated = transliterate_cyr_to_lat(lowered)
    no_accents = _strip_accents(transliterated)
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accents)
    tokens: set[str] = set()
    for raw_token in cleaned.split():
        if not raw_token:
            continue
        if raw_token in MATCH_STOPWORDS:
            continue
        if len(raw_token) <= 2 and not raw_token.isdigit():
            continue
        tokens.add(raw_token)
    return tokens


def token_match_score(user_tokens: set[str], alias_tokens: set[str]) -> float:
    if not user_tokens or not alias_tokens:
        return 0.0

    hits = 0.0
    for alias_token in alias_tokens:
        if alias_token in user_tokens:
            hits += 1.0 if not alias_token.isdigit() else 1.2
            continue

        # allow for simple suffix variations (e.g. kensington -> kensingtoni)
        if len(alias_token) >= 4:
            for user_token in user_tokens:
                if len(user_token) < 4:
                    continue
                if user_token.startswith(alias_token) or alias_token.startswith(user_token):
                    hits += 0.7
                    break

    return hits


def resolve_salon_from_text(app: Application, user_text: str) -> tuple[dict, float] | tuple[None, float]:
    match_index = app.bot_data.get("SALON_MATCH_INDEX") or []
    user_tokens = tokenize_for_match(user_text)
    best_entry = None
    best_score = 0.0

    for entry in match_index:
        score = token_match_score(user_tokens, entry["tokens"])
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= 0.8:
        return best_entry, best_score
    return None, 0.0

# ------------------------ GROUP LOCALIZATION --------------
GROUP_I18N = {
    "Волосся": {"en": "Hair", "uk": "Волосся", "ru": "Волосы"},
    "Нігтьовий сервіс": {"en": "Nail service", "uk": "Нігтьовий сервіс", "ru": "Ногтевой сервис"},
    "Візаж": {"en": "Makeup", "uk": "Візаж", "ru": "Визаж"},
    "Косметологія": {"en": "Cosmetology", "uk": "Косметологія", "ru": "Косметология"},
    "Масажі": {"en": "Massages", "uk": "Масажі", "ru": "Массажи"},
    "Чоловічі послуги": {"en": "Men's services", "uk": "Чоловічі послуги", "ru": "Мужские услуги"},
    "Подологія": {"en": "Podology", "uk": "Подологія", "ru": "Подология"},
    "Подарунки": {"en": "Gifts", "uk": "Подарунки", "ru": "Подарки"},
    "Догляд": {"en": "Treatment", "uk": "Догляд", "ru": "Уход"},
    "Консультації": {"en": "Consultations", "uk": "Консультації", "ru": "Консультации"},
    "Інше": {"en": "Other", "uk": "Інше", "ru": "Другое"},
}

# ------------------------ HAIR LENGTHS ------------------------
HAIR_LENGTH_OPTIONS = [
    {"id": "short",     "suffixes": {"en": "(Short)",       "uk": "(Коротке волосся)",   "ru": "(Короткие волосы)"}, "range": {"en": "2 - 6\"",  "uk": "2 - 6\"",  "ru": "2 - 6\""}},
    {"id": "medium",    "suffixes": {"en": "(Medium)",      "uk": "(Середнє волосся)",   "ru": "(Средние волосы)"},  "range": {"en": "6 - 10\"", "uk": "6 - 10\"", "ru": "6 - 10\""}},
    {"id": "long",      "suffixes": {"en": "(Long)",        "uk": "(Довге волосся)",     "ru": "(Длинные волосы)"},  "range": {"en": "10 - 14\"","uk": "10 - 14\"","ru": "10 - 14\""}},
    {"id": "extraLong", "suffixes": {"en": "(Extra Long)",  "uk": "(Дуже довге волосся)","ru": "(Очень длинные волосы)"}, "range": {"en": "14 - 20\"","uk": "14 - 20\"","ru": "14 - 20\""}},
    {"id": "tailbone",  "suffixes": {"en": "(Tailbone length)","uk": "(Довжина до куприка)","ru": "(Длина до копчика)"}, "range": {"en": "20 - 24\"","uk": "20 - 24\"","ru": "20 - 24\""}},
]

LENGTH_KEYWORDS = {
    "tailbone":   [r"tailbone", r"до\s*копчик", r"до\s*куприк", r"копчик", r"куприк"],
    "extraLong":  [r"extra\s*long", r"дуже\s*довг", r"очень\s*длин"],
    "long":       [r"\blong\b", r"довг(е|і|а)", r"длинн(ые|ая|ый|ые|ое)"],
    "medium":     [r"\bmedium\b", r"середн(є|і|я)", r"средн(ие|яя|ий|ее)"],
    "short":      [r"\bshort\b", r"коротк(е|і|ая|ие|ий)"],
}

def build_hair_length_labels(lang: str) -> Dict[str, str]:
    labels = {}
    for opt in HAIR_LENGTH_OPTIONS:
        suffix = opt["suffixes"][lang]
        rng = opt["range"][lang]
        if lang == "en":
            name = suffix.strip("()")
            label = f"{name} {rng}"
        else:
            label = f"{suffix} {rng}"
        labels[label] = opt["id"]
    return labels

_LANG_TAG = re.compile(r"^(EN|UA|UА|UK|RUS|RU)\s+", re.IGNORECASE)

def _norm_for_length(text: str) -> str:
    t = text or ""
    parts = [p.strip() for p in re.split(r"\s*/\s*", t) if p.strip()]
    cleaned = []
    for p in parts:
        p = _LANG_TAG.sub("", p.strip())
        p = re.sub(r"\s+", " ", p).lower()
        cleaned.append(p)
    return " | ".join(cleaned)

def detect_hair_length_tags(service_name: str) -> List[str]:
    text = _norm_for_length(service_name)
    found = []
    for length_id, patterns in LENGTH_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text):
                found.append(length_id)
                break
    return list(dict.fromkeys(found))

# ------------------------ MASTER LEVELS ------------------------
LEVEL_PATTERNS = {
    "top": [r"\btop\b", r"\bтоп\b"],
    "art": [r"\bart\b", r"\bарт\b"],
}
LEVEL_PRIORITY = ["top", "art", "master"]
LEVEL_BADGE = {
    "en": {"top": "⭐ TOP", "art": "🎨 ART", "master": "👩‍🎨 Master"},
    "uk": {"top": "⭐ ТОП", "art": "🎨 АРТ", "master": "👩‍🎨 Майстер"},
    "ru": {"top": "⭐ ТОП", "art": "🎨 АРТ", "master": "👩‍🎨 Мастер"},
}

def _normalize_all_lang_segments(text: str) -> str:
    parts = [p.strip() for p in re.split(r"\s*/\s*", text or "") if p.strip()]
    cleaned = []
    for p in parts:
        p = _LANG_TAG.sub("", p.strip())
        p = re.sub(r"\s+", " ", p).lower()
        cleaned.append(p)
    return " | ".join(cleaned)

def detect_master_level_from_text(text: str) -> str:
    t = _normalize_all_lang_segments(text)
    for lvl in ["top", "art"]:
        for pat in LEVEL_PATTERNS.get(lvl, []):
            if re.search(pat, t):
                return lvl
    return "master"

def detect_master_level(service: "ServiceItem") -> str:
    combined = f"{service.category} | {service.name}"
    return detect_master_level_from_text(combined)

def build_service_labels_with_levels(services_in_group: List["ServiceItem"], lang: str) -> Tuple[Dict[str, "ServiceItem"], List[str]]:
    base_map = build_service_labels(services_in_group, lang)
    labeled: Dict[str, ServiceItem] = {}
    level_of: Dict[str, str] = {}
    for base_label, svc in base_map.items():
        lvl = detect_master_level(svc)
        badge = LEVEL_BADGE.get(lang, LEVEL_BADGE["uk"]).get(lvl, "")
        final_label = f"{badge} · {base_label}"
        if final_label in labeled:
            final_label = f"{final_label} · {svc.id[-4:]}"
        labeled[final_label] = svc
        level_of[final_label] = lvl
    order_index = {lvl: i for i, lvl in enumerate(LEVEL_PRIORITY)}
    ordered_labels = sorted(labeled.keys(), key=lambda L: (order_index.get(level_of[L], 99), L.lower()))
    return labeled, ordered_labels

def localize_group(group_key: str, lang: str) -> str:
    return GROUP_I18N.get(group_key, {}).get(lang, group_key)

# ------------------------ MODELS --------------------------
@dataclass
class ServiceItem:
    id: str
    name: str
    category: str
    location_position: Optional[str]

@dataclass
class EmployeeItem:
    id: str
    name: str
    positions: List[str]

@dataclass
class Booking:
    group_name: Optional[str] = None
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    location_position: Optional[str] = None
    master_id: Optional[str] = None
    master_name: Optional[str] = None
    any_master: bool = False
    month_key: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    hair_length_id: Optional[str] = None

# ------------------------ NAME LOCALIZATION ---------------
SEG_SPLIT = re.compile(r"\s*/\s*")
def _norm_seg(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def parse_lang_variants(text: str) -> Dict[str, str]:
    langs = {}
    if not text:
        return langs
    parts = [p for p in SEG_SPLIT.split(text) if p and p.strip()]
    for p in parts:
        seg = _norm_seg(p)
        m = re.match(r"^(EN|UA|UА|UK|RUS|RU)\s+(.*)$", seg, re.IGNORECASE)
        if not m:
            continue
        tag = m.group(1).upper()
        val = _norm_seg(m.group(2))
        if tag in ("EN",):
            langs["en"] = val
        elif tag in ("UA", "UА", "UK"):
            langs["uk"] = val
        elif tag in ("RUS", "RU"):
            langs["ru"] = val
    return langs

def localize_name(text: str, lang: str) -> str:
    variants = parse_lang_variants(text)
    if lang in variants and variants[lang]:
        return variants[lang]
    for alt in ("uk", "en", "ru"):
        if alt in variants and variants[alt]:
            return variants[alt]
    return text or ""

# ------------------------ MONTH HELPERS -------------------
UA_MONTHS = ["січень","лютий","березень","квітень","травень","червень","липень","серпень","вересень","жовтень","листопад","грудень"]
def month_title_ua(year: int, m: int) -> str:
    return f"{UA_MONTHS[m-1].capitalize()} {year}"

def parse_month_choice(text: str, month_map: Dict[str, str]) -> Optional[str]:
    t = (text or "").strip().lower()
    for k, v in month_map.items():
        if t == v.lower() or t in v.lower():
            return k
    return None

def build_month_map(dates: Iterable[str]) -> Dict[str, str]:
    months = {}
    for d in dates:
        y, m, _ = d.split("-")
        months[f"{y}-{m}"] = month_title_ua(int(y), int(m))
    return dict(sorted(months.items(), key=lambda kv: kv[0]))

def days_in_month_from_dates(month_key: str, dates: Iterable[str]) -> List[str]:
    y, m = month_key.split("-")
    return sorted([d[-2:] for d in dates if d.startswith(f"{y}-{m}-")], key=lambda x: int(x))

# ------------------------ DATA LOADING --------------------
def _load_json(path: os.PathLike[str] | str):
    path_obj = Path(path)
    with path_obj.open("r", encoding="utf-8") as f:
        return json.load(f)

def ensure_data_loaded(app: Application):
    if "SALONS_META" in app.bot_data:
        return

    salons_path_env = os.getenv("SALONS_PATH")
    salons_path = Path(salons_path_env) if salons_path_env else Path("salons") / "salons.json"

    salons_meta = _load_json(salons_path)
    if not isinstance(salons_meta, list):
        raise ValueError("salons.json must contain a list of salons")

    app.bot_data["SALONS_META"] = salons_meta
    app.bot_data["SALONS_BY_FOLDER"] = {
        s.get("folder"): s for s in salons_meta if s.get("folder")
    }
    app.bot_data["SALONS_DATA"] = {}
    match_index = []
    for salon in salons_meta:
        folder = salon.get("folder")
        if not folder:
            continue

        alias_tokens: set[str] = set()
        for field in [
            salon.get("name"),
            salon.get("nameLocation"),
            salon.get("address", {}).get("line"),
        ]:
            alias_tokens.update(tokenize_for_match(field or ""))

        if not alias_tokens:
            alias_tokens.add(folder.lower())

        match_index.append({
            "folder": folder,
            "tokens": alias_tokens,
            "meta": salon,
        })

    app.bot_data["SALON_MATCH_INDEX"] = match_index
    default_folder = os.getenv("SALON_FOLDER") or (salons_meta[0]["folder"] if salons_meta else None)
    if default_folder:
        app.bot_data["DEFAULT_SALON_FOLDER"] = default_folder


def _resolve_salon_dir(folder: str) -> Path:
    override_dir = os.getenv("SALON_DATA_DIR")
    if override_dir:
        base = Path(override_dir)
        if (base / "services.json").exists():
            if base.name != folder and (base / folder / "services.json").exists():
                return base / folder
            return base
        return base / folder
    return Path("salons") / folder


def ensure_salon_data(app: Application, folder: str):
    if not folder:
        raise ValueError("Salon folder is required")

    cache = app.bot_data.setdefault("SALONS_DATA", {})
    if folder in cache:
        return cache[folder]

    salon_dir = _resolve_salon_dir(folder)

    def _resolve_path(env_key: str, default_name: str) -> Path:
        override = os.getenv(env_key)
        if override:
            return Path(override)
        return salon_dir / default_name

    services_path = _resolve_path("SERVICES_PATH", "services.json")
    employees_path = _resolve_path("EMPLOYEES_PATH", "employees.json")
    freetime_path = _resolve_path("FREETIME_PATH", "freetime.json")
    groups_path = _resolve_path("GROUPS_PATH", "groups_mapping.json")
    products_path = _resolve_path("PRODUCTS_PATH", "products.json")

    raw_services = _load_json(services_path)
    raw_employees = _load_json(employees_path)
    freetime: Dict[str, Dict[str, List[str]]] = _load_json(freetime_path)
    gm = _load_json(groups_path)
    raw_products: List[Dict] = []
    if products_path.exists():
        try:
            raw_products = _load_json(products_path)
        except Exception:
            log.exception("Failed to load products data from %%s", products_path)

    def is_addon_category(cat_name: str) -> bool:
        if not cat_name:
            return False
        c = cat_name.lower()
        return any(k in c for k in ["add-on", "addon"])

    services: List[ServiceItem] = [
        ServiceItem(
            id=s.get("id"),
            name=s.get("name") or "",
            category=s.get("category") or "",
            location_position=s.get("location_position"),
        )
        for s in raw_services
        if not s.get("archive", False) and not is_addon_category(s.get("category", ""))
    ]

    employees: Dict[str, EmployeeItem] = {
        e["id"]: EmployeeItem(
            id=e["id"],
            name=e.get("name") or "—",
            positions=e.get("positions") or [],
        )
        for e in raw_employees
        if not e.get("archive", False) and e.get("id")
    }

    grouped_services: Dict[str, List[ServiceItem]] = {}
    for s in services:
        grp = gm.get("category_to_group", {}).get(s.category)
        if not grp or "add-on" in (grp or "").lower() or "addon" in (grp or "").lower():
            continue
        grouped_services.setdefault(grp, []).append(s)

    available_groups = [g for g in gm.get("groups_order", []) if grouped_services.get(g)]
    for g in grouped_services:
        if g not in available_groups and grouped_services[g]:
            available_groups.append(g)

    cache[folder] = {
        "services": services,
        "grouped_services": grouped_services,
        "available_groups": available_groups,
        "employees": employees,
        "freetime": freetime,
        "products": raw_products,
    }
    log.info("Data loaded for %s. Groups: %s", folder, ", ".join(available_groups))
    return cache[folder]


def get_active_salon_data(context: ContextTypes.DEFAULT_TYPE):
    folder = context.user_data.get("SALON_FOLDER")
    log.info(f"User salon folder from context: {folder}")
    if not folder:
        folder = context.application.bot_data.get("DEFAULT_SALON_FOLDER")
        log.info(f"Using default salon folder: {folder}")
    if not folder:
        raise RuntimeError("Salon is not selected")
    log.info(f"Loading salon data for folder: {folder}")
    return ensure_salon_data(context.application, folder)

# ------------------------ AVAILABILITY --------------------
def employees_for_service(service: ServiceItem, employees: Dict[str, EmployeeItem]) -> List[EmployeeItem]:
    if not service.location_position:
        return []
    return [emp for emp in employees.values() if service.location_position in (emp.positions or [])]

def union_available_dates_for_emps(emp_list: List[EmployeeItem], freetime: Dict[str, Dict[str, List[str]]]) -> List[str]:
    today = date.today().strftime("%Y-%m-%d")
    out = set()
    for emp in emp_list:
        days = freetime.get(emp.id, {})
        for d, times in days.items():
            if times and d >= today:
                out.add(d)
    return sorted(out)

def available_dates_for_emp(emp: EmployeeItem, freetime: Dict[str, Dict[str, List[str]]]) -> List[str]:
    today = date.today().strftime("%Y-%m-%d")
    days = freetime.get(emp.id, {})
    return sorted([d for d, times in days.items() if times and d >= today])

# ------------------------ SERVICES PAGINATION -------------
PAGE_SIZE = 20

def build_service_labels(services_in_group: List[ServiceItem], lang: str) -> Dict[str, ServiceItem]:
    primary: Dict[str, List[ServiceItem]] = {}
    for s in services_in_group:
        lbl = localize_name(s.name, lang) or s.name
        primary.setdefault(lbl, []).append(s)

    labels: Dict[str, ServiceItem] = {}
    for lbl, items in primary.items():
        if len(items) == 1 and lbl not in labels:
            labels[lbl] = items[0]
        else:
            for s in items:
                cat_lbl = localize_name(s.category, lang) or s.category
                resolved = f"{lbl} ({cat_lbl})"
                if resolved in labels:
                    resolved = f"{resolved} · {s.id[-4:]}"
                labels[resolved] = s
    return labels

def make_rows(items: List[str], row: int = 1) -> List[List[str]]:
    return [items[i:i+row] for i in range(0, len(items), row)]

def services_page_kb(ctx: ContextTypes.DEFAULT_TYPE, labels: List[str], page: int) -> ReplyKeyboardMarkup:
    total = max(1, (len(labels) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = labels[start:end]
    nav = []

    prev_btn = T(ctx, "NAV_PREV")
    next_btn = T(ctx, "NAV_NEXT")
    if total > 1:
        row = []
        if page > 1:
            row.append(prev_btn)
        row.append(I18N[ctx.user_data.get("lang","uk")]["SERVICES_PAGE"].format(page=page, pages=total))
        if page < total:
            row.append(next_btn)
        nav = [row]

    return kb(make_rows(chunk, 1) + nav)

# ===== Helpers for DB logging =====
async def ensure_db_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Створює/оновлює користувача у БД, кладе його id в context.user_data['db_user_id']."""
    db_user_id = context.user_data.get("db_user_id")
    if db_user_id:
        return db_user_id
    u = update.effective_user
    tg_id = u.id if u else None
    first_name = (u.first_name or "") if u else ""
    username = (u.username or "") if u else ""
    lang = context.user_data.get("lang") or (u.language_code if u and u.language_code else "uk")
    db_user_id = await asyncio.to_thread(save_user_if_needed, tg_id, first_name, username, lang)
    context.user_data["db_user_id"] = db_user_id
    return db_user_id

async def log_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, text: Optional[str] = None):
    db_user_id = await ensure_db_user(update, context)
    msg = text if text is not None else (update.message.text if update.message else "")
    await asyncio.to_thread(save_message_log, db_user_id, "user", msg or "")

async def log_bot_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    db_user_id = await ensure_db_user(update, context)
    await asyncio.to_thread(save_message_log, db_user_id, "assistant", text or "")

_FAQ_CACHE: List[Dict] | None = None


def _load_faq_cache() -> List[Dict]:
    global _FAQ_CACHE
    if _FAQ_CACHE is not None:
        return _FAQ_CACHE

    faq_dir = Path("data/faq")
    entries: List[Dict] = []

    if faq_dir.exists():
        for path in sorted(faq_dir.glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                meta = {k: v for k, v in record.items() if k != "faq"}
                faq_items = record.get("faq", []) or []
                for item in faq_items:
                    question = (item.get("question") or "").strip()
                    answer = (item.get("answer") or "").strip()
                    tags = [t.strip() for t in (item.get("tags") or []) if isinstance(t, str)]
                    if not question or not answer:
                        continue
                    combined = " ".join([
                        question,
                        answer,
                        " ".join(tags),
                        " ".join(str(v) for v in meta.values() if isinstance(v, str)),
                    ]).lower()
                    entries.append({
                        "question": question,
                        "answer": answer,
                        "tags": tags,
                        "meta": meta,
                        "source": path.name,
                        "combined": combined,
                    })

    _FAQ_CACHE = entries
    return entries


def search_faq(query: str, max_results: int = 3) -> str:
    """Повертає релевантний витяг із FAQ для підказки мовній моделі."""
    query = (query or "").strip()
    if not query:
        return ""

    entries = _load_faq_cache()
    if not entries:
        return ""

    q_lower = query.lower()
    tokens = [t for t in re.findall(r"[\w'’]+", q_lower) if len(t) > 2]

    scored: List[Tuple[float, Dict]] = []
    for entry in entries:
        score = 0.0
        combined = entry["combined"]

        if q_lower in combined:
            score += 5.0

        if tokens:
            token_hits = sum(1 for t in tokens if t in combined)
            score += float(token_hits)

        tags_lower = [t.lower() for t in entry.get("tags", [])]
        for token in tokens:
            if token in tags_lower:
                score += 1.5

        meta = entry.get("meta", {})
        brand = str(meta.get("brand", "") or meta.get("category", "") or "").lower()
        if brand and brand in q_lower:
            score += 2.0

        if score > 0:
            scored.append((score, entry))

    if not scored:
        return ""

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:max_results]

    lines = [
        "FAQ KNOWLEDGE BASE (Top matches):",
        "Use this curated knowledge when responding to related questions.",
    ]
    for _, entry in top:
        meta = entry.get("meta", {})
        context_bits = []
        if meta.get("brand"):
            context_bits.append(f"Brand: {meta['brand']}")
        if meta.get("description"):
            context_bits.append(f"Summary: {meta['description'][:200]}")
        if meta.get("category") and not meta.get("brand"):
            context_bits.append(f"Category: {meta['category']}")
        if meta.get("segment"):
            context_bits.append(f"Segment: {meta['segment']}")

        header = " | ".join(context_bits)
        if header:
            lines.append(f"- {header}")
        lines.append(f"  Q: {entry['question']}")
        lines.append(f"  A: {entry['answer']}")

    return "\n".join(lines)

# ------------------------ INTENT / AI SNAPSHOT ------------
def _detect_product_location_id(products: List[Dict]) -> Optional[str]:
    for product in products:
        prices = product.get("location_prices")
        if isinstance(prices, list):
            for entry in prices:
                loc = entry.get("location")
                if loc:
                    return str(loc)
    for product in products:
        stocks = product.get("stocks")
        if isinstance(stocks, dict):
            for loc in stocks.keys():
                if loc:
                    return str(loc)
    return None


def _extract_product_price(product: Dict, location_id: Optional[str]) -> Optional[float]:
    prices = product.get("location_prices")
    if isinstance(prices, list):
        chosen: Optional[Dict] = None
        if location_id:
            for entry in prices:
                if str(entry.get("location")) == str(location_id):
                    chosen = entry
                    break
        if chosen is None:
            chosen = next((entry for entry in prices if isinstance(entry, dict)), None)
        if chosen:
            for key in ("price", "portion_price", "unit_price", "original_price", "original_portion_price", "original_unit_price"):
                val = chosen.get(key)
                if isinstance(val, (int, float)) and val and val > 0:
                    return float(val)
    elif isinstance(prices, (int, float)) and prices > 0:
        return float(prices)

    supply_price = product.get("supply_price")
    if isinstance(supply_price, (int, float)) and supply_price > 0:
        return float(supply_price)
    return None


def _extract_product_stock(product: Dict, location_id: Optional[str]) -> Optional[float]:
    stocks = product.get("stocks")
    if isinstance(stocks, dict):
        if location_id and str(location_id) in stocks:
            val = stocks.get(str(location_id))
            if isinstance(val, (int, float)):
                return float(val)
        total = 0.0
        has_value = False
        for val in stocks.values():
            if isinstance(val, (int, float)):
                total += float(val)
                has_value = True
        if has_value:
            return total
    return None


def _format_product_volume(product: Dict) -> str:
    volume = product.get("volume")
    units = product.get("units")
    if isinstance(volume, (int, float)) and volume:
        if isinstance(units, str) and units:
            return f"{volume:g} {units}"
        return f"{volume:g}"
    return ""


# ============================================================
# Embedding-based smart service search
# ============================================================

# Кешовані embeddings послуг по кожному салону
_SERVICE_EMBED_CACHE: dict = {}


def _get_relevant_services(user_text: str, salon_folder: str = None) -> str:
    """
    Розумний пошук по послугах за допомогою AI-embeddings.
    AI сам розуміє синоніми, переклади та формулювання.
    """
    global _SERVICE_EMBED_CACHE

    try:
        if not user_text or len(user_text.strip()) < 3:
            return ""

        salon_dir = _resolve_salon_dir(salon_folder) if salon_folder else Path("salons/salon2")
        services_path = salon_dir / "services.json"
        if not services_path.exists():
            return ""

        salon_key = str(salon_dir.resolve())
        mtime = services_path.stat().st_mtime

        # Якщо файл змінився – перераховуємо embeddings
        cache = _SERVICE_EMBED_CACHE.get(salon_key)
        if not cache or cache.get("mtime") != mtime:
            raw = _load_json(services_path)
            services = []
            texts = []

            for svc in raw:
                if svc.get("archive") or "add-on" in (svc.get("category") or "").lower():
                    continue

                name = svc.get("name") or ""
                category = svc.get("category") or ""
                desc = (
                    svc.get("description")
                    or svc.get("description_uk")
                    or svc.get("description_ru")
                    or ""
                )

                if not name.strip():
                    continue

                full_text = f"{name}. Category: {category}. {desc}"
                texts.append(full_text)
                services.append(svc)

            if not services:
                return ""

            vectors = embed_texts(texts)

            _SERVICE_EMBED_CACHE[salon_key] = {
                "mtime": mtime,
                "services": services,
                "vectors": vectors,
            }
            cache = _SERVICE_EMBED_CACHE[salon_key]

        services = cache["services"]
        vectors = cache["vectors"]

        # Embedding запиту
        qvec_list = embed_texts(user_text)
        if not qvec_list:
            return ""
        qvec = qvec_list[0]

        # Косинусна схожість
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        scored = [(cos(qvec, vec), svc) for svc, vec in zip(services, vectors)]
        scored.sort(key=lambda x: x[0], reverse=True)

        MIN_SCORE = 0.55
        lines = []

        for score, svc in scored[:8]:
            if score < MIN_SCORE:
                continue

            name = svc.get("name")
            duration = svc.get("duration")
            price = None

            lp = svc.get("location_prices")
            if isinstance(lp, (int, float)):
                price = lp
            elif isinstance(lp, dict):
                try:
                    price = next(iter(lp.values()))
                except StopIteration:
                    pass

            if duration and price:
                lines.append(f"• {name} — £{price}, {duration}min")
            elif duration:
                lines.append(f"• {name} — {duration}min")
            elif price:
                lines.append(f"• {name} — £{price}")
            else:
                lines.append(f"• {name}")

        return "\n".join(lines) if lines else ""

    except Exception as e:
        log.warning(f"_get_relevant_services error: {e}")
        return ""


def _snap_services_for_ai(data: Dict, salon_folder: str = None) -> str:
    snapshot = (
        "I have access to all salon services. When you mention what you need, "
        "I'll find relevant options and ask clarifying questions to select the "
        "exact service for booking.\n"
    )

    # Базова структура рівнів
    by_level = {"Master": 0, "TOP": 0, "ART": 0}
    masters_count_by_level = {}

    try:
        # Припускаємо, що ці дані приходять у `data`
        services_by_level = data.get("services_by_level", {})
        raw_services = data.get("raw_services", [])
        raw_employees = data.get("raw_employees", [])
        salon_dir = data.get("salon_dir")

        # 1. Проходимо по всім послугам і перевіряємо, хто може їх виконувати
        for level in ["Master", "TOP", "ART"]:
            level_services = services_by_level.get(level, [])
            available_masters = set()
            
            for svc in level_services:
                # Створюємо ServiceItem для пошуку майстрів
                service_item = ServiceItem(
                    id="temp",
                    name=svc["name"],
                    category=svc["category"],
                    location_position=next(
                        (
                            s.get("location_position")
                            for s in raw_services
                            if s.get("name") == svc["name"]
                        ),
                        None,
                    ),
                )
                
                # Створюємо employees dict для пошуку майстрів
                employees_dict = {
                    e["id"]: EmployeeItem(
                        id=e["id"],
                        name=e.get("name") or "—",
                        positions=e.get("positions") or [],
                    )
                    for e in raw_employees
                    if not e.get("archive", False) and e.get("id")
                }
                
                # Знаходимо майстрів для цієї послуги
                service_masters = employees_for_service(service_item, employees_dict)
                for master in service_masters:
                    available_masters.add(master.name)
            
            masters_count_by_level[level] = len(available_masters)
            if available_masters:
                snapshot += f"{level}: {len(available_masters)} masters available\n"
                for master in list(available_masters)[:3]:  # показуємо перших 3
                    snapshot += f"  - {master}\n"
            else:
                snapshot += f"{level}: 0 masters available\n"
        
        # 2. Додаємо детальну інформацію про майстрів та їх спеціалізації
        # Створюємо мапу майстрів за послугами
        masters_by_service = {}
        
        snapshot += "\nMASTERS AND THEIR SPECIALIZATIONS:\n"
        for emp in raw_employees:
            if emp.get("archive", False):
                continue
            name = emp.get("name", "")
            position_names = emp.get("position_names", [])
            if name and position_names:
                snapshot += f"{name}: {', '.join(position_names)}\n"
            
            # Групуємо майстрів за типами послуг
            for pos in position_names:
                pos_lower = pos.lower()
                if "nail" in pos_lower or "pedicure" in pos_lower:
                    masters_by_service.setdefault("manicure", []).append(name)
                elif "hair" in pos_lower or "colorist" in pos_lower or "haircut" in pos_lower:
                    masters_by_service.setdefault("hair", []).append(name)
                elif "makeup" in pos_lower:
                    masters_by_service.setdefault("makeup", []).append(name)
                elif (
                    "massage" in pos_lower
                    or "icoone" in pos_lower
                    or "stratosphere" in pos_lower
                ):
                    masters_by_service.setdefault("massage", []).append(name)
        
        # 3. Читаємо categories.json з повною структурою
        categories_path = salon_dir / "categories.json"
        if categories_path.exists():
            categories = _load_json(categories_path)
            snapshot += "\n=== SERVICE CATEGORIES (hierarchical structure) ===\n"
            snapshot += (
                "Use this to understand service organization "
                "and suggest related services\n\n"
            )
            
            categories_by_parent = {}
            for cat in categories:
                if cat.get("archive", False):
                    continue
                parent = cat.get("parentName") or "Root"
                categories_by_parent.setdefault(parent, []).append(cat)
            
            for parent, cats in sorted(categories_by_parent.items())[:20]:
                if parent != "Root":
                    snapshot += f"\n{parent}:\n"
                for cat in cats[:10]:
                    name = cat.get("name", "")
                    if name and not any(
                        x in name.lower() for x in ["add-on", "addon"]
                    ):
                        snapshot += f"  - {name}\n"
        
        # 3.1 Читаємо clients_search.json для розуміння історії клієнтів
        clients_path = salon_dir / "clients_search.json"
        if clients_path.exists():
            try:
                clients_data = _load_json(clients_path)
                snapshot += "\n=== CLIENT DATA AVAILABLE ===\n"
                snapshot += f"Total clients in database: {len(clients_data)}\n"
                snapshot += "When client provides phone/email, you can:\n"
                snapshot += "- Check their visit history\n"
                snapshot += "- See their preferred masters\n"
                snapshot += "- View past services\n"
                snapshot += "- Check loyalty status (Active/OneTime/etc)\n"
                snapshot += (
                    "Use find_client_appointments tool to access this data\n\n"
                )
            except Exception as e:  # noqa: F841
                log.warning(f"Failed to load clients data: {e}")
        
        # 4. Читаємо freetime.json (детальна інфо)
        freetime_path = salon_dir / "freetime.json"
        if freetime_path.exists():
            freetime = _load_json(freetime_path)
            snapshot += f"\nAVAILABILITY: {len(freetime)} masters have schedules\n"
            
            # Додаємо інформацію про доступність на завтра та найближчі дати
            from datetime import date, timedelta, datetime
            
            tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            snapshot += f"\nTOMORROW ({tomorrow}) AVAILABILITY:\n"
            masters_with_time_tomorrow = 0
            
            # Створюємо мапу майстрів за іменами для швидкого пошуку
            masters_by_name = {}
            for emp in raw_employees:
                if not emp.get("archive", False) and emp.get("name"):
                    name = emp.get("name", "").lower()
                    masters_by_name[name] = emp
            
            for master_id, schedule in freetime.items():
                if tomorrow in schedule and schedule[tomorrow]:
                    # Знаходимо ім'я майстра
                    master_name = next(
                        (
                            emp.get("name", "")
                            for emp in raw_employees
                            if emp.get("id") == master_id
                        ),
                        "Unknown",
                    )
                    if master_name and master_name != "Unknown":
                        times = schedule[tomorrow]
                        snapshot += (
                            f"{master_name}: {len(times)} slots "
                            f"({', '.join(times[:3])}"
                            f"{'...' if len(times) > 3 else ''})\n"
                        )
                        masters_with_time_tomorrow += 1
            
            if masters_with_time_tomorrow == 0:
                snapshot += "No masters available tomorrow\n"
            
            # Додаємо детальну інформацію про конкретних майстрів
            snapshot += "\nSPECIFIC MASTERS AVAILABILITY:\n"
            
            # Перевіряємо популярних майстрів
            popular_masters = ["belei marianna", "marianna", "маріанна"]
            
            for master_name_search in popular_masters:
                found_master = None
                for name, emp in masters_by_name.items():
                    if master_name_search in name or any(
                        part in name for part in master_name_search.split()
                    ):
                        found_master = emp
                        break
                
                if found_master:
                    master_id = found_master.get("id")
                    master_name = found_master.get("name")
                    
                    if master_id in freetime:
                        schedule = freetime[master_id]
                        # Знаходимо найближчу доступну дату
                        available_dates = []
                        today = date.today().strftime("%Y-%m-%d")
                        
                        for date_str, times in schedule.items():
                            if date_str >= today and times:
                                available_dates.append((date_str, times))
                        
                        if available_dates:
                            # Сортуємо за датою
                            available_dates.sort(key=lambda x: x[0])
                            next_date, next_times = available_dates[0]
                            
                            # Форматуємо дату для відображення
                            try:
                                date_obj = datetime.strptime(
                                    next_date, "%Y-%m-%d"
                                )
                                formatted_date = (
                                    date_obj.strftime("%d %B")
                                    .replace("January", "січня")
                                    .replace("February", "лютого")
                                    .replace("March", "березня")
                                    .replace("April", "квітня")
                                    .replace("May", "травня")
                                    .replace("June", "червня")
                                    .replace("July", "липня")
                                    .replace("August", "серпня")
                                    .replace("September", "вересня")
                                    .replace("October", "жовтня")
                                    .replace("November", "листопада")
                                    .replace("December", "грудня")
                                )
                            except Exception:
                                formatted_date = next_date
                            
                            snapshot += (
                                f"{master_name}: NEXT AVAILABLE {formatted_date} "
                                f"at {', '.join(next_times[:5])}"
                                f"{'...' if len(next_times) > 5 else ''}\n"
                            )
                            
                            # Додаємо ще кілька найближчих дат
                            if len(available_dates) > 1:
                                for i, (date_str, times) in enumerate(
                                    available_dates[1:4], 1
                                ):
                                    try:
                                        date_obj = datetime.strptime(
                                            date_str, "%Y-%m-%d"
                                        )
                                        formatted_date = (
                                            date_obj.strftime("%d %B")
                                            .replace("January", "січня")
                                            .replace("February", "лютого")
                                            .replace("March", "березня")
                                            .replace("April", "квітня")
                                            .replace("May", "травня")
                                            .replace("June", "червня")
                                            .replace("July", "липня")
                                            .replace("August", "серпня")
                                            .replace("September", "вересня")
                                            .replace("October", "жовтня")
                                            .replace("November", "листопада")
                                            .replace("December", "грудня")
                                        )
                                    except Exception:
                                        formatted_date = date_str
                                    snapshot += (
                                        f"  Also available: {formatted_date} "
                                        f"at {', '.join(times[:3])}"
                                        f"{'...' if len(times) > 3 else ''}\n"
                                    )
                        else:
                            snapshot += f"{master_name}: NO AVAILABLE DATES\n"
                    break  # Знайшли майстра, виходимо з циклу
            
            # Додаємо інформацію про майстрів за послугами
            snapshot += "\nMASTERS BY SERVICE TYPE:\n"
            for service_type, masters in masters_by_service.items():
                unique_masters = list(set(masters))
                snapshot += f"{service_type.upper()}: {', '.join(unique_masters)}\n"
            
            # Додаємо правила пошуку майстрів
            snapshot += "\nBOOKING RULES:\n"
            snapshot += "1. For MANICURE/PEDICURE - only suggest Nail Technicians\n"
            snapshot += "2. For HAIR services - only suggest Hair Stylists/Colorists\n"
            snapshot += "3. For MAKEUP - only suggest Makeup Artists\n"
            snapshot += "4. For MASSAGE - only suggest Massage therapists\n"
            snapshot += (
                "5. NEVER suggest masters who don't do the requested service\n"
            )
            snapshot += (
                "6. Check master exists in 'MASTERS AND THEIR SPECIALIZATIONS' "
                "section\n"
            )
            snapshot += (
                "7. Use SERVICE → MASTERS → SCHEDULE logic, not random "
                "suggestions\n"
            )
        
        # FAQ тимчасово відключено

    except Exception as e:
        snapshot = f"Error loading salon data: {e}\n"
    
    return snapshot


# ------------------------ BACKGROUND TASKS -------------------------
async def create_appointment_background_task(update: Update, context: ContextTypes.DEFAULT_TYPE, arguments: Dict):
    """Асинхронне створення запису в CRM з аргументів AI"""
    try:
        log.info(f"🔄 Починаємо фонове створення запису: {arguments.get('client_name')}")
        
        # Імпортуємо функцію створення запису
        from salons.create_appointments import create_appointment
        
        # Формуємо дані для CRM
        booking_data = {
            "client": {
                "name": arguments["client_name"],
                "phone": arguments["client_phone"],
                "email": arguments["client_email"],
                "location": arguments["location_id"]
            },
            "services": [{
                "date": arguments["date"],
                "time": arguments["time"],
                "duration": arguments["duration"],
                "price": arguments["price"],
                "employeeId": arguments["employee_id"],
                "serviceId": arguments["service_id"]
            }]
        }
        
        # Створюємо запис в CRM
        crm_result = await asyncio.to_thread(
            create_appointment,
            booking_data,
            database_code=arguments["database_code"],
            location_id=arguments["location_id"]
        )
        
        appointment_id = crm_result.get('appointment', {}).get('id')
        log.info(f"✅ Запис успішно створено в CRM: {appointment_id}")
        
        # Надсилаємо підтвердження клієнту
        lang = context.user_data.get("lang", "uk")
        if lang == "uk":
            success_msg = "✅ Запис успішно створено в системі!"
        elif lang == "ru":
            success_msg = "✅ Запись успешно создана в системе!"
        else:
            success_msg = "✅ Appointment successfully created in the system!"
        
        await context.bot.send_message(chat_id=update.effective_chat.id, text=success_msg)
        
        # Зберігаємо запис в локальну БД
        try:
            db_user_id = context.user_data.get("db_user_id")
            if db_user_id:
                await asyncio.to_thread(
                    save_booking,
                    db_user_id,
                    "",  # group_name
                    arguments.get("service_id", ""),
                    None,  # hair_length_id
                    arguments.get("employee_id", ""),
                    arguments.get("date", ""),
                    arguments.get("time", ""),
                    "confirmed",
                )
                log.info(f"✅ Запис збережено в локальну БД")
        except Exception as e:
            log.warning(f"⚠️ Не вдалося зберегти в локальну БД: {e}")
        
    except Exception as e:
        log.error(f"❌ Помилка фонового створення запису: {e}")
        
        # Надсилаємо повідомлення про помилку
        lang = context.user_data.get("lang", "uk")
        if lang == "uk":
            error_msg = "⚠️ Виникла помилка при створенні запису в системі. Ми зв'яжемось з вами для підтвердження."
        elif lang == "ru":
            error_msg = "⚠️ Возникла ошибка при создании записи в системе. Мы свяжемся с вами для подтверждения."
        else:
            error_msg = "⚠️ An error occurred while creating the appointment. We will contact you for confirmation."
        
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error_msg)
        except Exception:
            pass  # Ігноруємо помилки відправки повідомлень

async def create_appointment_background(update: Update, context: ContextTypes.DEFAULT_TYPE, booking: Booking, client_profile: Dict):
    """Фонове створення запису в CRM (застаріла функція)"""
    try:
        salon_folder = context.user_data.get("SALON_FOLDER")
        if not salon_folder:
            return
        
        salon_config = next((s for s in SALONS if s.get("folder") == salon_folder), None)
        if not salon_config:
            return
        
        phone = client_profile.get("phone", "")
        email = client_profile.get("email", "")
        
        booking_data = {
            "client": {
                "name": client_profile.get("name", "Client"),
                "location": salon_config.get("location_sales"),
                "phone": phone,
                "email": email,
            },
            "services": [{
                "date": booking.date,
                "time": booking.time,
                "duration": booking.duration or 60,
                "price": booking.price or 0,
                "employeeId": booking.master_id,
                "serviceId": booking.service_id,
            }]
        }
        
        crm_result = await asyncio.to_thread(
            create_appointment,
            booking_data,
            database_code=salon_config["database_code"],
            location_id=salon_config.get("location_sales")
        )
        
        appointment_id = crm_result.get('appointment', {}).get('id')
        log.info(f"✅ CRM appointment created in background: {appointment_id}")
        
        # Надсилаємо підтвердження
        lang = context.user_data.get("lang", "uk")
        if lang == "uk":
            msg = "✅ Запис підтверджено системою!"
        elif lang == "ru":
            msg = "✅ Запись подтверждена системой!"
        else:
            msg = "✅ Appointment confirmed by the system!"
        
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
        
    except Exception as e:
        log.error(f"❌ Background CRM appointment failed: {e}")

# ------------------------ HANDLERS -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ініціалізація БД (ідемпотентно)
    init_db()

    # почистимо попередній стан користувача
    context.user_data.clear()
    try:
        ensure_data_loaded(context.application)
    except Exception as e:
        log.exception("Failed to load data")
        await update.message.reply_text(f"Data load error: {e}")
        return ConversationHandler.END

    # лог юзерського /start
    await log_user_msg(update, context)

    context.user_data["lang"] = "en"  # ask in English
    reply = "Hi! Please choose your language:\n\nEnglish / Українська / Русский"
    await update.message.reply_text(reply, reply_markup=kb([["English","Українська","Русский"]]))
    await log_bot_msg(update, context, reply)
    return LANGUAGE

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    choice = (update.message.text or "").strip()
    lang = LANG_CODES.get(choice)
    if not lang:
        reply = "Please choose your language from the buttons."
        await update.message.reply_text(reply, reply_markup=kb([["English","Українська","Русский"]]))
        await log_bot_msg(update, context, reply)
        return LANGUAGE
    context.user_data["lang"] = lang

    salons_meta: List[Dict] = context.application.bot_data.get("SALONS_META", [])
    salon_labels: List[str] = []
    salon_map: Dict[str, str] = {}
    for salon in salons_meta:
        folder = salon.get("folder")
        if not folder:
            continue
        label = salon.get("name") or salon.get("nameLocation") or folder
        if not label:
            continue
        salon_map[label] = folder
        salon_labels.append(label)

    if salon_labels:
        context.user_data["SALON_LABELS"] = salon_map
        context.user_data["SALON_LABELS_LIST"] = salon_labels
        reply = f"{T(context,'ASK_SALON')}"
        rows = [salon_labels[i:i+2] for i in range(0, len(salon_labels), 2)]
        if not rows and salon_labels:
            rows = [[label] for label in salon_labels]
        await update.message.reply_text(reply, reply_markup=kb(rows))
        await log_bot_msg(update, context, reply)
        return SALON

    # fallback: no salons metadata loaded, підключаємо AI Міру
    if lang == "uk":
        connecting_msg = "Підключаємо менеджера для консультації..."
    elif lang == "ru":
        connecting_msg = "Подключаем менеджера для консультации..."
    else:
        connecting_msg = "Connecting consultant..."
    
    await update.message.reply_text(connecting_msg, reply_markup=ReplyKeyboardRemove())
    await log_bot_msg(update, context, connecting_msg)
    
    # Затримка 5 секунд
    await asyncio.sleep(2)
    
    if lang == "uk":
        greeting = "Вітаю!)💛 Мене звати Міра, онлайн консультант PdP! Чим можу допомогти?)"
    elif lang == "ru":
        greeting = "Привет!)💛 Меня зовут Мира, онлайн консультант PdP! Чем могу помочь?)"
    else:
        greeting = "Hello!)💛 My name is Mira, PdP online consultant! How can I help you?)"
    
    await update.message.reply_text(greeting)
    await log_bot_msg(update, context, greeting)
    return FREE_QUESTION

async def handle_salon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip().lower()
    labels_map: Dict[str, str] = context.user_data.get("SALON_LABELS", {})
    labels_list: List[str] = context.user_data.get("SALON_LABELS_LIST", list(labels_map.keys()))
    folder = None

    # Спочатку перевіряємо числівники (перший/другий/1/2/first/second)
    ordinal_map = {
        "1": 0, "перший": 0, "первый": 0, "first": 0, "один": 0, "one": 0,
        "2": 0, "другий": 1, "второй": 1, "second": 1, "два": 1, "two": 1,
        "3": 2, "третій": 2, "третий": 2, "third": 2, "три": 2, "three": 2,
    }

    # Виправлення: "2" має бути індексом 1
    ordinal_map["2"] = 1

    if text in ordinal_map:
        idx = ordinal_map[text]
        if idx < len(labels_list):
            folder = labels_map.get(labels_list[idx])
            log.info(f"🏠 Salon selected by ordinal '{text}' -> index {idx} -> {folder}")

    # Якщо не числівник - шукаємо по назві
    if not folder:
        original_text = (update.message.text or "").strip()
        if original_text in labels_map:
            folder = labels_map[original_text]
        else:
            for label, fldr in labels_map.items():
                if normalize(original_text) == normalize(label):
                    folder = fldr
                    break

    if not folder:
        reply = T(context, "CHOOSE_FROM_KB")
        labels_list: List[str] = context.user_data.get("SALON_LABELS_LIST", list(labels_map.keys()))
        rows = [labels_list[i:i+2] for i in range(0, len(labels_list), 2)]
        if not rows and labels_list:
            rows = [[label] for label in labels_list]
        await update.message.reply_text(reply, reply_markup=kb(rows))
        await log_bot_msg(update, context, reply)
        return SALON

    context.user_data["SALON_FOLDER"] = folder
    salon_meta = context.application.bot_data.get("SALONS_BY_FOLDER", {}).get(folder)
    if salon_meta:
        context.user_data["SALON_META"] = salon_meta

    # Підключаємо AI Міру після вибору салону
    lang = context.user_data.get("lang", "uk")
    if lang == "uk":
        connecting_msg = "Підключаємо менеджера для консультації..."
    elif lang == "ru":
        connecting_msg = "Подключаем менеджера для консультации..."
    else:
        connecting_msg = "Connecting consultant..."
    
    await update.message.reply_text(connecting_msg, reply_markup=ReplyKeyboardRemove())
    await log_bot_msg(update, context, connecting_msg)
    
    # Затримка 5 секунд
    await asyncio.sleep(2)
    
    if lang == "uk":
        greeting = "Вітаю! Мене звати Міра, онлайн помічник PdP! Чим можу допомогти?"
    elif lang == "ru":
        greeting = "Привет! Меня зовут Мира, онлайн помощник PdP! Чем могу помочь?"
    else:
        greeting = "Hello! My name is Mira, PdP online assistant! How can I help you?"
        
    await update.message.reply_text(greeting)
    await log_bot_msg(update, context, greeting)
    return FREE_QUESTION

async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    data = get_active_salon_data(context)
    labels_map: Dict[str, str] = context.user_data.get("GROUP_LABELS", {})

    picked_key = None
    if text in labels_map:
        picked_key = labels_map[text]
    else:
        for label, gkey in labels_map.items():
            if normalize(text) in normalize(label):
                picked_key = gkey
                break

    if not picked_key:
        lang = context.user_data.get("lang", "uk")
        labels = list(labels_map.keys())
        reply = T(context, "NOT_RECOGNIZED")
        await update.message.reply_text(reply, reply_markup=kb([labels[i:i+2] for i in range(0, len(labels), 2)]))
        await log_bot_msg(update, context, reply)
        return GROUP

    booking = Booking(group_name=picked_key)
    context.user_data["booking"] = booking

    lang = context.user_data.get("lang", "uk")

    if picked_key != "Волосся":
        services_in_group: List[ServiceItem] = data["grouped_services"].get(picked_key, [])
        if not services_in_group:
            labels = list(labels_map.keys())
            reply = T(context, "NO_MATCHES")
            await update.message.reply_text(reply, reply_markup=kb([labels[i:i+2] for i in range(0, len(labels), 2)]))
            await log_bot_msg(update, context, reply)
            return GROUP

        labels_map_services, service_labels = build_service_labels_with_levels(services_in_group, lang)
        context.user_data["SERVICES_IN_GROUP"] = labels_map_services
        context.user_data["SERVICES_LABELS_LIST"] = service_labels
        context.user_data["SERVICES_PAGE"] = 1

        reply = f"✅ {localize_group(picked_key, lang)}\n{T(context,'LEVEL_HINT')}\n{T(context,'ASK_SERVICE_PICK')}"
        await update.message.reply_text(reply, reply_markup=services_page_kb(context, service_labels, page=1))
        await log_bot_msg(update, context, reply)
        return SERVICE_PICK

    # Hair flow
    length_labels = build_hair_length_labels(lang)
    context.user_data["HAIR_LENGTH_LABELS"] = length_labels

    reply = f"✅ {localize_group(picked_key, lang)}\n{T(context,'ASK_HAIR_LENGTH')}"
    await update.message.reply_text(reply, reply_markup=kb([[lbl] for lbl in list(length_labels.keys())]))
    await log_bot_msg(update, context, reply)
    return HAIR_LENGTH

async def handle_hair_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    labels: Dict[str, str] = context.user_data.get("HAIR_LENGTH_LABELS", {})
    length_id = None
    if text in labels:
        length_id = labels[text]
    else:
        for lbl, lid in labels.items():
            if normalize(text) in normalize(lbl):
                length_id = lid
                break

    if not length_id:
        reply = T(context, "NOT_RECOGNIZED")
        await update.message.reply_text(reply, reply_markup=kb([[lbl] for lbl in list(labels.keys())]))
        await log_bot_msg(update, context, reply)
        return HAIR_LENGTH

    booking: Booking = context.user_data.get("booking", Booking())
    booking.hair_length_id = length_id
    context.user_data["booking"] = booking

    data = get_active_salon_data(context)
    all_services: List[ServiceItem] = data["grouped_services"].get("Волосся", [])

    def include_service(s: ServiceItem) -> bool:
        tags = set(detect_hair_length_tags(s.name))
        if not tags:
            return True
        return tags == {length_id}

    filtered = [s for s in all_services if include_service(s)]
    if not filtered:
        reply = T(context, "NO_MATCHES")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        reply2 = T(context, "ASK_HAIR_LENGTH")
        await update.message.reply_text(reply2, reply_markup=kb([[lbl] for lbl in list(labels.keys())]))
        await log_bot_msg(update, context, reply2)
        return HAIR_LENGTH

    lang = context.user_data.get("lang", "uk")
    labels_map_services, service_labels = build_service_labels_with_levels(filtered, lang)
    context.user_data["SERVICES_IN_GROUP"] = labels_map_services
    context.user_data["SERVICES_LABELS_LIST"] = service_labels
    context.user_data["SERVICES_PAGE"] = 1

    reply = f"✂ {localize_group('Волосся', lang)} — {text}\n{T(context,'LEVEL_HINT')}\n{T(context,'ASK_SERVICE_PICK')}"
    await update.message.reply_text(reply, reply_markup=services_page_kb(context, service_labels, page=1))
    await log_bot_msg(update, context, reply)
    return SERVICE_PICK

async def handle_service_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    lang = context.user_data.get("lang", "uk")

    if text in (T(context,"NAV_PREV"), T(context,"NAV_NEXT")) or text.startswith(I18N[lang]["SERVICES_PAGE"].split("{")[0].strip()):
        labels = context.user_data.get("SERVICES_LABELS_LIST", [])
        page = context.user_data.get("SERVICES_PAGE", 1)
        total = max(1, (len(labels) + PAGE_SIZE - 1) // PAGE_SIZE)
        if text == T(context, "NAV_PREV"):
            page = max(1, page - 1)
        elif text == T(context, "NAV_NEXT"):
            page = min(total, page + 1)
        context.user_data["SERVICES_PAGE"] = page
        reply = T(context,'ASK_SERVICE_PICK')
        await update.message.reply_text(reply, reply_markup=services_page_kb(context, labels, page))
        await log_bot_msg(update, context, reply)
        return SERVICE_PICK

    labels_map: Dict[str, ServiceItem] = context.user_data.get("SERVICES_IN_GROUP", {})
    picked = labels_map.get(text)
    if not picked:
        norm = normalize(text)
        for lbl, svc in labels_map.items():
            if norm and norm in normalize(lbl):
                picked = svc
                break
    if not picked:
        labels = context.user_data.get("SERVICES_LABELS_LIST", [])
        page = context.user_data.get("SERVICES_PAGE", 1)
        reply = T(context, "NOT_RECOGNIZED")
        await update.message.reply_text(reply, reply_markup=services_page_kb(context, labels, page))
        await log_bot_msg(update, context, reply)
        return SERVICE_PICK

    booking: Booking = context.user_data.get("booking", Booking())
    booking.service_id = picked.id
    booking.service_name = picked.name
    booking.location_position = picked.location_position
    context.user_data["booking"] = booking

    reply = T(context, "ASK_MASTER_MODE")
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=kb([[T(context, "SPECIFIC_MASTER_BTN")], [T(context, "ANY_MASTER_BTN")]]))
    await log_bot_msg(update, context, reply)
    return MASTER_MODE

async def handle_master_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    user_text_raw = (update.message.text or "").strip()
    user_text = user_text_raw.lower()
    lang = context.user_data.get("lang", "uk")

    specific_btn = I18N[lang]["SPECIFIC_MASTER_BTN"].lower()
    any_btn      = I18N[lang]["ANY_MASTER_BTN"].lower()

    booking: Booking = context.user_data.get("booking", Booking())

    if user_text == specific_btn:
        booking.any_master = False
    elif user_text == any_btn:
        booking.any_master = True
    else:
        tokens_any = ["any", "будь", "любой", "по дате", "за датою"]
        booking.any_master = any(t in user_text for t in tokens_any)

    context.user_data["booking"] = booking

    data = get_active_salon_data(context)
    employees: Dict[str, EmployeeItem] = data["employees"]

    all_emps = employees_for_service(
        ServiceItem(id=booking.service_id or "", name=booking.service_name or "", category="", location_position=booking.location_position),
        employees,
    )
    if not all_emps:
        reply = T(context, "NO_EMPLOYEES")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        return ConversationHandler.END

    context.user_data["ALL_EMPS"] = all_emps

    if booking.any_master:
        freetime = data["freetime"]
        all_dates = union_available_dates_for_emps(all_emps, freetime)
        if not all_dates:
            reply = T(context, "NO_SLOTS")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return ConversationHandler.END
        month_map = build_month_map(all_dates)
        context.user_data["AVAIL_DATES"] = all_dates
        context.user_data["MONTH_MAP"] = month_map
        reply = T(context, "ASK_MONTH")
        await update.message.reply_text(reply, reply_markup=kb([[v] for v in list(month_map.values())]))
        await log_bot_msg(update, context, reply)
        return MONTH
    else:
        reply = T(context, "ASK_MASTER_PICK")
        await update.message.reply_text(reply, reply_markup=kb([[e.name] for e in all_emps]))
        await log_bot_msg(update, context, reply)
        return MASTER_PICK

async def handle_master_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    name = (update.message.text or "").strip()
    all_emps: List[EmployeeItem] = context.user_data.get("ALL_EMPS", [])
    picked = next((e for e in all_emps if normalize(name) in normalize(e.name)), None)
    if not picked:
        reply = T(context, "NOT_RECOGNIZED") + " " + T(context, "ASK_MASTER_PICK")
        await update.message.reply_text(reply, reply_markup=kb([[e.name] for e in all_emps]))
        await log_bot_msg(update, context, reply)
        return MASTER_PICK

    booking: Booking = context.user_data.get("booking", Booking())
    booking.master_id = picked.id
    booking.master_name = picked.name
    context.user_data["booking"] = booking

    data = get_active_salon_data(context)
    freetime = data["freetime"]
    dates = available_dates_for_emp(picked, freetime)
    if not dates:
        reply = T(context, "NO_SLOTS")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        return MASTER_PICK

    month_map = build_month_map(dates)
    context.user_data["AVAIL_DATES"] = dates
    context.user_data["MONTH_MAP"] = month_map
    reply = T(context, "ASK_MONTH")
    await update.message.reply_text(reply, reply_markup=kb([[v] for v in list(month_map.values())]))
    await log_bot_msg(update, context, reply)
    return MONTH

async def handle_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    month_map: Dict[str, str] = context.user_data.get("MONTH_MAP", {})
    month_key = parse_month_choice(text, month_map)
    if not month_key:
        reply = T(context, "PICK_MONTH_AGAIN")
        await update.message.reply_text(reply, reply_markup=kb([[v] for v in list(month_map.values())]))
        await log_bot_msg(update, context, reply)
        return MONTH

    booking: Booking = context.user_data.get("booking", Booking())
    booking.month_key = month_key
    context.user_data["booking"] = booking

    dates = context.user_data.get("AVAIL_DATES", [])
    days = days_in_month_from_dates(month_key, dates)
    if not days:
        reply = T(context, "NO_SLOTS")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        return MONTH

    reply = T(context, "ASK_DAY")
    await update.message.reply_text(reply, reply_markup=kb([days[i:i+6] for i in range(0, len(days), 6)]))
    await log_bot_msg(update, context, reply)
    return DAY

async def handle_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    day_text = (update.message.text or "").strip().zfill(2)
    booking: Booking = context.user_data.get("booking", Booking())
    dates = context.user_data.get("AVAIL_DATES", [])
    if not booking.month_key:
        reply = T(context, "PICK_MONTH_AGAIN")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        return MONTH

    candidate_date = None
    for d in dates:
        if d.startswith(booking.month_key + "-") and d.endswith("-" + day_text):
            candidate_date = d
            break
    if not candidate_date:
        reply = T(context, "PICK_DAY_AGAIN")
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)
        return DAY

    booking.date = candidate_date
    context.user_data["booking"] = booking

    data = get_active_salon_data(context)
    freetime: Dict[str, Dict[str, List[str]]] = data["freetime"]

    if booking.any_master:
        all_emps: List[EmployeeItem] = context.user_data.get("ALL_EMPS", [])
        emp_times_for_day: Dict[str, List[str]] = {}
        unique_times: set[str] = set()
        for emp in all_emps:
            times = freetime.get(emp.id, {}).get(booking.date, []) or []
            if times:
                emp_times_for_day[emp.id] = times
                unique_times.update(times)

        if not unique_times:
            reply = T(context, "NO_SLOTS")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return DAY

        context.user_data["EMP_TIMES_FOR_DAY"] = emp_times_for_day
        times_list = sorted(unique_times, key=lambda t: (int(t.split(":")[0]), int(t.split(":")[1])))
        context.user_data["UNIQUE_TIMES_FOR_DAY"] = times_list

        reply = T(context, "ASK_TIME_PICK")
        await update.message.reply_text(reply, reply_markup=kb([times_list[i:i+3] for i in range(0, len(times_list), 3)]))
        await log_bot_msg(update, context, reply)
        return TIME_PICK

    else:
        times = freetime.get(booking.master_id or "", {}).get(booking.date, [])
        if not times:
            reply = T(context, "NO_SLOTS")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return DAY
        context.user_data["TIMES_FOR_MASTER"] = times
        reply = T(context, "ASK_TIME_PICK")
        await update.message.reply_text(reply, reply_markup=kb([times[i:i+3] for i in range(0, len(times), 3)]))
        await log_bot_msg(update, context, reply)
        return TIME_PICK

async def handle_time_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    booking: Booking = context.user_data.get("booking", Booking())
    data = get_active_salon_data(context)
    freetime: Dict[str, Dict[str, List[str]]] = data["freetime"]

    if booking.any_master:
        times_list: List[str] = context.user_data.get("UNIQUE_TIMES_FOR_DAY", [])
        emp_times_for_day: Dict[str, List[str]] = context.user_data.get("EMP_TIMES_FOR_DAY", {})
        all_emps: List[EmployeeItem] = context.user_data.get("ALL_EMPS", [])
        emp_by_id: Dict[str, EmployeeItem] = {e.id: e for e in all_emps}

        if text not in times_list:
            reply = T(context, "PICK_TIME_AGAIN")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return TIME_PICK

        candidates = [emp_id for emp_id, times in emp_times_for_day.items() if text in times]
        if not candidates:
            reply = T(context, "PICK_TIME_AGAIN")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return TIME_PICK

        counts = {emp_id: len(emp_times_for_day.get(emp_id, [])) for emp_id in candidates}
        max_count = max(counts.values())
        top_emp_ids = [eid for eid, cnt in counts.items() if cnt == max_count]
        chosen_emp_id = random.choice(top_emp_ids)
        chosen_emp = emp_by_id.get(chosen_emp_id)

        log.info("📅 Auto-assign by date mode")
        log.info(f"Chosen service: {booking.service_name}")
        log.info(f"Chosen date: {booking.date}, time: {text}")
        log.info(f"Candidates ({len(candidates)}):")
        for eid in candidates:
            emp = emp_by_id.get(eid)
            log.info(f"  - {emp.name if emp else eid}: {counts[eid]} free slots")
        if len(top_emp_ids) > 1:
            log.info(f"Equal counts, random chosen from: {[emp_by_id[e].name for e in top_emp_ids]}")
        log.info(f"✅ Assigned master: {chosen_emp.name if chosen_emp else chosen_emp_id}")

        booking.master_id = chosen_emp_id
        booking.master_name = chosen_emp.name if chosen_emp else "—"
        booking.time = text
        context.user_data["booking"] = booking

    else:
        times = context.user_data.get("TIMES_FOR_MASTER", [])
        if text not in times:
            reply = T(context, "PICK_TIME_AGAIN")
            await update.message.reply_text(reply)
            await log_bot_msg(update, context, reply)
            return TIME_PICK
        booking.time = text
        context.user_data["booking"] = booking

    lang = context.user_data.get("lang", "uk")
    service_local = localize_name(booking.service_name or "", lang) or (booking.service_name or "—")

    lang = context.user_data.get("lang", "uk")
    service_local = localize_name(booking.service_name or "", lang) or (booking.service_name or "—")

    # ✅ ВІДПРАВЛЯЄМО ПІДТВЕРДЖЕННЯ КЛІЄНТУ ОДРАЗУ
    reply = T(context, "CONFIRM").format(
        service=service_local,
        master=booking.master_name or "—",
        date=booking.date or "—",
        time=booking.time or "—",
    )
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await log_bot_msg(update, context, reply)

    # При використанні AI чату створення запису відбувається асинхронно через AI інструменти
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    reply = T(context, "CANCEL_TEXT")
    await update.message.reply_text(reply, reply_markup=ReplyKeyboardRemove())
    await log_bot_msg(update, context, reply)
    return ConversationHandler.END


# Глобальний словник для зберігання таймерів користувачів
_user_timers = {}

async def _process_buffered_messages(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обробка буферизованих повідомлень після закінчення таймера"""
    try:
        buffer = context.application.bot_data.get("msg_buffer", {}).get(user_id)
        if not buffer:
            return
        
        all_messages = buffer["messages"]
        all_photos = buffer["photos"]
        
        if not all_messages:
            return
        
        # Очищаємо буфер
        context.application.bot_data["msg_buffer"][user_id] = {"messages": [], "last_time": 0, "photos": [], "task": None}
        
        combined_text = "\n".join(all_messages)
        log.info(f"📦 Processing {len(all_messages)} messages together: {combined_text[:100]}...")
        
        # Показуємо typing індикатор перед обробкою AI
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
        
        # Тут буде вся логіка обробки AI з handle_free_question
        await _process_ai_response(update, context, combined_text, all_photos)
    finally:
        # Видаляємо таймер з глобального словника
        _user_timers.pop(user_id, None)

async def handle_free_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    incoming_text = (update.message.text or update.message.caption or "").strip()

    # Очищаємо кеш lookup_services при новому повідомленні від користувача
    clear_lookup_cache()

    db_user_id = context.user_data.get("db_user_id")
    if db_user_id:
        ai_enabled = await asyncio.to_thread(is_ai_enabled, db_user_id)
        if not ai_enabled:
            await log_user_msg(update, context, incoming_text)
            return FREE_QUESTION
    
    user_id = update.effective_user.id if update.effective_user else 0
    current_time = time.time()
    
    # Ініціалізуємо буфер
    if "msg_buffer" not in context.application.bot_data:
        context.application.bot_data["msg_buffer"] = {}
    
    if user_id not in context.application.bot_data["msg_buffer"]:
        context.application.bot_data["msg_buffer"][user_id] = {"messages": [], "last_time": 0, "photos": [], "task": None}
    
    # Скасовуємо попередній таймер якщо він є
    if user_id in _user_timers:
        old_task = _user_timers[user_id]
        if not old_task.done():
            old_task.cancel()
            log.info(f"⏱️ Cancelled previous timer for user {user_id}")
    
    # Додаємо повідомлення
    context.application.bot_data["msg_buffer"][user_id]["messages"].append(incoming_text)
    context.application.bot_data["msg_buffer"][user_id]["last_time"] = time.time()
    
    # Додаємо фото якщо є
    pending_images = context.user_data.get("pending_images")
    if pending_images:
        context.application.bot_data["msg_buffer"][user_id]["photos"].extend(pending_images)
        context.user_data.pop("pending_images", None)
    
    await log_user_msg(update, context, incoming_text)
    
    # Створюємо новий таймер
    async def timer_task():
        await asyncio.sleep(8)
        await _process_buffered_messages(update, context, user_id)
    
    task = asyncio.create_task(timer_task())
    _user_timers[user_id] = task
    log.info(f"⏱️ Started 8s timer for user {user_id}")
    
    return FREE_QUESTION

async def _process_ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE, incoming_text: str, pending_images: list = None):
    """Обробка AI відповіді (винесена логіка з handle_free_question)"""
    # Показуємо typing тільки коли починаємо обробку AI
    try:
        await update.message.chat.send_action(action="typing")
    except Exception:
        pass  # Ігноруємо помилки typing індикатора
    
    pending_text_override = incoming_text
    pending_log_override = None
    
    lang = context.user_data.get("lang", "uk")
    q = incoming_text
    
    if _is_examples_request(q):
        # Затримка 5 секунд перед відправкою заготовленого повідомлення
        await asyncio.sleep(2)
        instagram_replies = {
            "uk": "Щоб було зручніше, усі приклади наших робіт зібрано в нашому інстаграм 👉 @pied.de.poule_london — там ми постійно додаємо щось нове 💇‍♀️✨",
            "ru": "Чтобы было удобнее, все примеры наших работ собраны в нашем инстаграм 👉 @pied.de.poule_london — туда мы постоянно добавляем что-то новое 💇‍♀️✨",
            "en": "For convenience, all examples of our work are collected in our Instagram 👉 @pied.de.poule_london — we keep adding fresh looks there 💇‍♀️✨",
        }
        reply = instagram_replies.get(lang, instagram_replies["uk"])
        await update.message.reply_text(reply)
        await log_bot_msg(update, context, reply)

        # Позначаємо, що показали приклади робіт для AI
        context.user_data["showed_examples"] = True
        
        # Продовжуємо обробку через AI для консультації
        # Не робимо return, щоб AI міг відповісти далі

    # Перевіряємо чи клієнт хоче почати спочатку - скидаємо вибір салону
    start_over_patterns = ["спочатку", "почнемо", "start over", "начать заново", "заново", "з початку"]
    if any(pattern in incoming_text.lower() for pattern in start_over_patterns):
        if context.user_data.get("SALON_FOLDER"):
            log.info(f"🔄 Client wants to start over - clearing SALON_FOLDER: {context.user_data.get('SALON_FOLDER')}")
            context.user_data.pop("SALON_FOLDER", None)
            context.user_data.pop("SALON_META", None)

    # Перевіряємо чи користувач вибирає салон у повідомленні
    # ЗАВЖДИ перевіряємо числівники - клієнт може змінювати вибір салону!
    ordinal_map = {
        "1": 0, "перший": 0, "первый": 0, "first": 0, "один": 0, "one": 0,
        "другий": 1, "второй": 1, "second": 1, "два": 1, "two": 1, "2": 1,
        "третій": 2, "третий": 2, "third": 2, "три": 2, "three": 2, "3": 2,
    }
    text_lower = incoming_text.strip().lower()
    salons_meta_list = context.application.bot_data.get("SALONS_META", [])

    # Якщо клієнт явно вибирає салон числівником - ЗАВЖДИ оновлюємо
    if text_lower in ordinal_map and salons_meta_list:
        idx = ordinal_map[text_lower]
        if idx < len(salons_meta_list):
            salon = salons_meta_list[idx]
            folder = salon.get("folder")
            old_folder = context.user_data.get("SALON_FOLDER")
            context.user_data["SALON_FOLDER"] = folder
            context.user_data["SALON_META"] = salon
            if old_folder and old_folder != folder:
                log.info(f"🏠 Salon CHANGED by ordinal '{text_lower}': {old_folder} -> {folder}")
            else:
                log.info(f"🏠 Salon selected by ordinal '{text_lower}' -> index {idx} -> {folder}")
    elif not context.user_data.get("SALON_FOLDER"):
        # Якщо салон ще не обраний - пробуємо token matching
        salon_match, score = resolve_salon_from_text(context.application, incoming_text)
        if salon_match and score >= 0.8:
            folder = salon_match.get("folder")
            context.user_data["SALON_FOLDER"] = folder
            context.user_data["SALON_META"] = salon_match.get("meta")
            log.info(f"✅ Auto-detected salon from text: {folder} (score: {score})")
    
    # Логуємо поточний стан салону
    salon_folder = context.user_data.get("SALON_FOLDER")
    salon_meta = context.user_data.get("SALON_META")
    log.info(f"handle_free_question: salon_folder={salon_folder}, salon_meta={salon_meta.get('name') if salon_meta else None}")

    # Завантажуємо профіль клієнта (з кешуванням)
    user = update.effective_user
    client_profile = None
    if user:
        chat_id = str(user.id)
        # Перевіряємо кеш
        cache_key = f"client_profile_{chat_id}"
        client_profile = context.user_data.get(cache_key)
        
        if not client_profile:
            try:
                client_profile = await asyncio.to_thread(get_beauty_client, chat_id, "telegram")
                if client_profile:
                    # Кешуємо на 5 хвилин
                    context.user_data[cache_key] = client_profile
                    context.user_data[f"{cache_key}_time"] = time.time()
                    log.info(f"Loaded and cached client profile for chat_id={chat_id}")
            except Exception as e:
                log.warning(f"Failed to load client profile: {e}")
        else:
            # Перевіряємо чи не застарів кеш (5 хвилин)
            cache_time = context.user_data.get(f"{cache_key}_time", 0)
            if time.time() - cache_time > 300:  # 5 хвилин
                context.user_data.pop(cache_key, None)
                context.user_data.pop(f"{cache_key}_time", None)
                client_profile = None
    
    # Розпізнавання намірів скасування/перенесення та запису
    snapshot = ""
    cancel_keywords = ["скасувати", "скасувати запис", "відмінити", "відмінити запис", "cancel", "cancel appointment", "отменить", "отменить запись"]
    reschedule_keywords = ["перенести", "перенести запис", "змінити час", "змінити дату", "reschedule", "change time", "change date", "перенести запись", "изменить время"]
    booking_keywords = ["записатися", "запис", "хочу записатися", "потрібен запис", "book", "booking", "appointment", "записаться", "нужна запись"]
    check_appointment_keywords = ["мій запис", "мої записи", "коли мій запис", "перевірити запис", "my appointment", "check appointment", "мой визит", "моя запись"]
    
    is_cancel_intent = any(kw in incoming_text.lower() for kw in cancel_keywords)
    is_reschedule_intent = any(kw in incoming_text.lower() for kw in reschedule_keywords)
    is_booking_intent = any(kw in incoming_text.lower() for kw in booking_keywords)
    is_check_appointment_intent = any(kw in incoming_text.lower() for kw in check_appointment_keywords)
    
    # Пошук клієнта потрібен тільки при конкретних намірах
    needs_client_search = is_cancel_intent or is_reschedule_intent or is_check_appointment_intent
    
    # Логуємо розпізнані наміри
    if needs_client_search:
        intent_type = "cancel" if is_cancel_intent else "reschedule" if is_reschedule_intent else "check"
        log.info(f"✅ Розпізнано намір '{intent_type}' - пошук клієнта дозволено")
    else:
        log.info(f"❌ Намір не потребує пошуку клієнта - звичайна консультація")
    
    # Якщо потрібен пошук клієнта (скасування, перенесення, перевірка записів)
    if needs_client_search:
        # Перевіряємо чи є контактні дані клієнта
        phone = None
        email = None
        
        if client_profile:
            phone_numbers = client_profile.get('phone_numbers')
            if phone_numbers and isinstance(phone_numbers, list) and len(phone_numbers) > 0:
                phone = phone_numbers[0]
            
            emails = client_profile.get('emails')
            if emails and isinstance(emails, list) and len(emails) > 0:
                email = emails[0]
        
        # Якщо немає контактів - AI попросить їх
        if not phone and not email:
            if is_cancel_intent:
                context.user_data["pending_cancel_or_reschedule"] = "cancel"
            elif is_reschedule_intent:
                context.user_data["pending_cancel_or_reschedule"] = "reschedule"
            else:
                context.user_data["pending_check_appointments"] = True
        else:
            # Шукаємо клієнта в CRM
            salon_folder = context.user_data.get("SALON_FOLDER")
            if salon_folder:
                salon_config = next((s for s in SALONS if s.get("folder") == salon_folder), None)
                if salon_config:
                    try:
                        crm_client = await asyncio.to_thread(
                            find_client_by_contact,
                            salon_config["database_code"],
                            phone=phone,
                            email=email
                        )
                        
                        if crm_client:
                            client_id = crm_client.get("id")
                            # Отримуємо записи клієнта
                            from datetime import date
                            today = date.today().strftime("%Y-%m-%d")
                            appointments = await asyncio.to_thread(
                                get_client_appointments,
                                salon_config["database_code"],
                                client_id,
                                from_date=today
                            )
                            
                            if appointments:
                                # Зберігаємо записи для подальшої обробки
                                context.user_data["client_appointments"] = appointments
                                if is_cancel_intent:
                                    context.user_data["pending_cancel_or_reschedule"] = "cancel"
                                elif is_reschedule_intent:
                                    context.user_data["pending_cancel_or_reschedule"] = "reschedule"
                                else:
                                    context.user_data["pending_check_appointments"] = True
                                
                                # Додаємо інформацію про записи в контекст для AI
                                appointments_info = "\n\nCLIENT APPOINTMENTS FOUND:\n"
                                for i, apt in enumerate(appointments, 1):
                                    apt_date = apt.get("date", "")
                                    services = apt.get("services", [])
                                    if services:
                                        for svc in services:
                                            svc_name = svc.get("serviceName", "Unknown")
                                            master_name = svc.get("professionalName", "Unknown")
                                            start_time = svc.get("start", "")[:5] if svc.get("start") else ""
                                            appointments_info += f"{i}. {apt_date} {start_time} - {svc_name} з {master_name}\n"
                                
                                snapshot = appointments_info
                            else:
                                # Немає записів - AI повідомить про це
                                snapshot = "\n\nNO APPOINTMENTS FOUND for this client\n"
                        else:
                            # Клієнт не знайдений - AI попросить уточнити дані
                            snapshot = "\n\nCLIENT NOT FOUND in CRM by provided contact info\n"
                    except Exception as e:
                        log.error(f"Error finding client appointments: {e}")
                        snapshot = "\n\nERROR finding client appointments\n"
    
    # беремо дані активного салону тільки якщо він обраний
    salon_folder = context.user_data.get("SALON_FOLDER")
    salon_meta = context.user_data.get("SALON_META", {})
    if not snapshot:
        snapshot = ""

    # Базова інформація про салон + розумний пошук послуг
    # ТІЛЬКИ якщо салон вже обраний - завантажуємо його дані
    if salon_folder:
        try:
            # Основна інформація про салон
            snapshot = _snap_services_for_ai({}, salon_folder)

            # Додаємо релевантні послуги якщо є запит
            if len(incoming_text.split()) > 1:
                relevant_services = _get_relevant_services(incoming_text, salon_folder)
                if relevant_services:
                    snapshot += f"\n\nRelevant services found:\n{relevant_services}"

        except Exception as e:
            log.warning(f"Помилка завантаження даних салону {salon_folder}: {e}")
            snapshot = "Salon data temporarily unavailable"
    else:
        # Салон НЕ обраний - не завантажуємо дані конкретного салону
        snapshot = ""
        log.info("Salon not selected yet - skipping salon data loading")
    
    # Додаємо структуровану інформацію про профіль клієнта (ПРІОРИТЕТ)
    client_context = ""
    if client_profile:
        client_context = "\n=== CLIENT PROFILE (CRITICAL - USE THIS DATA FIRST) ===\n"
        
        # Контактні дані
        if client_profile.get('phone_numbers'):
            phones = client_profile['phone_numbers']
            client_context += f"📞 Phone: {', '.join(phones)}\n"
            client_context += f"   → Use this phone to check appointments in CRM\n"
        
        if client_profile.get('emails'):
            emails = client_profile['emails']
            client_context += f"📧 Email: {', '.join(emails)}\n"
        
        # Улюблені салони
        if client_profile.get('favorite_salons'):
            salons = client_profile['favorite_salons']
            client_context += f"🏢 Favorite salons: {', '.join(salons)}\n"
        
        # Улюблені майстри
        if client_profile.get('favorite_masters'):
            masters = client_profile['favorite_masters']
            client_context += f"💇 Favorite masters: {', '.join(masters)}\n"
            client_context += f"   → Suggest these masters first when booking\n"
        
        # Улюблені послуги
        if client_profile.get('favorite_services'):
            services = client_profile['favorite_services']
            client_context += f"✨ Favorite services: {', '.join(services)}\n"
        
        # Звичайний час візитів
        if client_profile.get('usual_day_time'):
            time_pref = client_profile['usual_day_time']
            client_context += f"⏰ Usual visit time: {time_pref}\n"
            client_context += f"   → Suggest times in this period first\n"
        
        # Обмеження/алергії
        if client_profile.get('restrictions'):
            restrictions = client_profile['restrictions']
            client_context += f"⚠️ Restrictions/allergies: {restrictions}\n"
            client_context += f"   → ALWAYS consider these restrictions\n"
        
        client_context += "\n🎯 PERSONALIZATION RULES:\n"
        client_context += "- ALWAYS use phone from profile to check appointments\n"
        client_context += "- DON'T ask for phone if it's already in profile\n"
        client_context += "- Suggest favorite masters and services first\n"
        client_context += "- Respect time preferences and restrictions\n"
        client_context += "=== END CLIENT PROFILE ===\n\n"
    
    # Додаємо інформацію про обраний салон або список салонів для вибору
    if salon_meta:
        salon_info = f"\nSALON ALREADY CHOSEN:\n"
        salon_info += f"Name: {salon_meta.get('name', 'Unknown')}\n"
        salon_info += f"Location: {salon_meta.get('nameLocation', 'Unknown')}\n"
        if salon_meta.get('address'):
            address_line = salon_meta.get('address', {}).get('line') if isinstance(salon_meta.get('address'), dict) else salon_meta.get('address')
            salon_info += f"Address: {address_line}\n"
        snapshot = client_context + salon_info + snapshot
    else:
        # Салон НЕ обраний - додаємо інструкцію для AI запитати салон
        try:
            ensure_data_loaded(context.application)
            salons_meta_list = context.application.bot_data.get("SALONS_META", [])
            if salons_meta_list:
                salon_choice_info = "\n=== SALON NOT SELECTED - MUST ASK FIRST ===\n"
                salon_choice_info += "CRITICAL: Client has NOT chosen a salon yet!\n"
                salon_choice_info += "Before discussing services, times, or booking - you MUST ask which salon they want.\n\n"
                salon_choice_info += "Available salons:\n"
                for i, s in enumerate(salons_meta_list, 1):
                    name = s.get("name") or s.get("nameLocation") or s.get("folder")
                    location = s.get("nameLocation", "")
                    address = s.get("address", {})
                    if isinstance(address, dict):
                        address_line = address.get("line", "")
                    else:
                        address_line = address or ""
                    salon_choice_info += f"{i}. {name}"
                    if location and location != name:
                        salon_choice_info += f" ({location})"
                    if address_line:
                        salon_choice_info += f" - {address_line}"
                    salon_choice_info += "\n"
                salon_choice_info += "\nASK the client: 'В якому салоні хочете записатися?' and list the options.\n"
                salon_choice_info += "=== END SALON SELECTION ===\n\n"
                snapshot = client_context + salon_choice_info + snapshot
            else:
                snapshot = client_context + snapshot
        except Exception:
            snapshot = client_context + snapshot
    
    # ЗАВЖДИ завантажуємо історію для перевірки вітання
    db_user_id = await ensure_db_user(update, context)
    recent_messages = await asyncio.to_thread(fetch_recent_messages, db_user_id, 30)
    log.info(f"📄 Завантажено історію: {len(recent_messages)} повідомлень")
    history_text = ""
    
    # Шукаємо салон в історії розмови якщо він ще не обраний
    if not context.user_data.get("SALON_FOLDER"):
        for msg in recent_messages:
            msg_text = msg.get("text", "")
            salon_match, score = resolve_salon_from_text(context.application, msg_text)
            if salon_match and score >= 0.8:
                folder = salon_match.get("folder")
                context.user_data["SALON_FOLDER"] = folder
                context.user_data["SALON_META"] = salon_match.get("meta")
                log.info(f"✅ Found salon in history: {folder} (score: {score})")
                break
    
    # Формуємо історію розмови
    chat_history = []
    for msg in recent_messages:
        role = msg.get("role", "user")
        text = msg.get("text", "")
        if role == "user":
            chat_history.append(f"User: {text}")
        else:
            chat_history.append(f"Mira: {text}")
    
    history_text = "\n".join(chat_history) if chat_history else ""

    # Додаємо інформацію про салони та обраний салон
    context_info = {}
    
    # Додаємо список доступних салонів для контексту
    try:
        ensure_data_loaded(context.application)
        salons_meta = context.application.bot_data.get("SALONS_META", [])
        if salons_meta:
            salon_list = []
            for i, salon in enumerate(salons_meta, 1):
                name = salon.get("name") or salon.get("nameLocation") or salon.get("folder")
                if name:
                    salon_list.append(f"{i}. {name}")
            if salon_list:
                context_info["available_salons"] = salon_list
    except Exception:
        pass
    
    if salon_meta:
        context_info["salon_chosen"] = True
        context_info["salon_name"] = salon_meta.get('name', '')
        context_info["salon_location"] = salon_meta.get('nameLocation', '')
        if salon_meta.get('address'):
            address_line = salon_meta.get('address', {}).get('line') if isinstance(salon_meta.get('address'), dict) else salon_meta.get('address')
            context_info["salon_address"] = address_line
        
        # Додаємо технічні дані салону для створення записів
        salon_folder = context.user_data.get("SALON_FOLDER") or context.user_data.get("salon_folder")
        if salon_folder:
            # CRITICAL: salon_folder потрібен для lookup_services
            context_info["salon_folder"] = salon_folder
            salon_config = next((s for s in SALONS if s.get("folder") == salon_folder), None)
            if salon_config:
                context_info["database_code"] = salon_config.get("database_code")
                # ВАЖЛИВО: salon_location_id - це ID САЛОНУ для CRM, НЕ для refresh_freetime!
                # Для refresh_freetime потрібен location_position ПОСЛУГИ з lookup_services!
                context_info["salon_location_id_FOR_CRM_ONLY"] = salon_config.get("location_sales")
    
    faq_section = search_faq(q)
    
    # формуємо промпт для OpenAI
    context_summary = ""
    if context_info:
        context_summary = f"SALON CONTEXT: {context_info}\n\n"
    
    # Додаємо інформацію про показ прикладів робіт
    showed_examples = context.user_data.get("showed_examples", False)
    if showed_examples:
        context_summary += "EXAMPLES SHOWN: User just saw our work examples, now start consultation process\n\n"
    
    # Визначаємо чи це кілька повідомлень (об'єднаних буфером)
    is_multiple_messages = "\n" in q

    if is_multiple_messages:
        user_msg_section = f"USER MESSAGES (multiple, answer ALL of them):\n{q}\n\nIMPORTANT: User sent multiple messages. Address EACH request/question in your response."
    else:
        user_msg_section = f"USER MESSAGE:\n{q}"

    prompt = (
        context_summary
        + (f"CONVERSATION HISTORY (last 30 messages):\n{history_text}\n\n" if history_text else "")
        + f"{user_msg_section}\n\n"
        + ("USER SHARED AN IMAGE: Analyze it for relevant context.\n\n" if pending_images else "")
        + f"LANGUAGE: {lang}\n\n"
        + (faq_section + "\n\n" if faq_section else "")
    )







    # викликаємо клієнт OpenAI з інструментами для перевірки записів
    try:
        lang = context.user_data.get("lang", "en")
        
        # Отримуємо database_code активного салону
        salon_folder = context.user_data.get("SALON_FOLDER")
        database_code = None
        if salon_folder:
            salon_config = next((s for s in SALONS if s.get("folder") == salon_folder), None)
            if salon_config:
                database_code = salon_config.get("database_code")
        
        # Викликаємо OpenAI з інструментами
        # Створюємо таск для періодичного оновлення typing індикатора
        typing_task = None
        try:
            # Змінна для контролю typing під час refresh_freetime
            refresh_freetime_in_progress = False
            # Зберігаємо посилання на головний event loop для використання в іншому потоці
            main_loop = asyncio.get_running_loop()

            async def keep_typing():
                while True:
                    try:
                        # Не показуємо typing під час оновлення freetime
                        if not refresh_freetime_in_progress:
                            await update.message.chat.send_action(action="typing")
                    except Exception:
                        pass  # Ігноруємо помилки typing індикатора
                    await asyncio.sleep(4)

            typing_task = asyncio.create_task(keep_typing())

            # Прапорець для відстеження чи вже відправили "Хвилинку..."
            freetime_message_sent = False

            # Створюємо асинхронний executor для інструментів
            def async_tool_executor(tool_name: str, arguments: dict):
                nonlocal refresh_freetime_in_progress, freetime_message_sent

                # Для create_appointment використовуємо асинхронний режим
                if tool_name == "create_appointment":
                    # Запускаємо створення запису у фоні через main_loop
                    asyncio.run_coroutine_threadsafe(
                        create_appointment_background_task(update, context, arguments),
                        main_loop
                    )
                    # Одразу повертаємо успіх
                    return {
                        "success": True,
                        "message": "Запис підтверджено! Створюється в системі...",
                        "async": True
                    }
                elif tool_name == "refresh_freetime":
                    # Для refresh_freetime вимикаємо typing і відправляємо повідомлення
                    refresh_freetime_in_progress = True
                    try:
                        # Відправляємо повідомлення ТІЛЬКИ ОДИН РАЗ за сесію
                        if not freetime_message_sent:
                            lang = context.user_data.get("lang", "uk")
                            if lang == "ru":
                                check_msg = "Минуту, проверяю расписание 🙂"
                            elif lang == "en":
                                check_msg = "One moment, checking the schedule 🙂"
                            else:
                                check_msg = "Хвилинку, перевіряю графік 🙂"

                            # Відправляємо через asyncio (виконується в іншому потоці)
                            # Використовуємо збережений main_loop замість get_event_loop()
                            asyncio.run_coroutine_threadsafe(
                                update.message.reply_text(check_msg),
                                main_loop
                            ).result(timeout=5)
                            freetime_message_sent = True

                        # Виконуємо оновлення freetime
                        result = execute_tool(tool_name, arguments)
                        return result
                    finally:
                        refresh_freetime_in_progress = False
                else:
                    # Для інших інструментів використовуємо звичайний режим
                    return execute_tool(tool_name, arguments)
            
            if pending_images:
                result = await asyncio.to_thread(
                    openai_reply_with_usage,
                    prompt,
                    lang,
                    pending_images,
                    tools=APPOINTMENT_TOOLS,
                    tool_executor=async_tool_executor,
                    database_code=database_code,
                    salon_snapshot=snapshot
                )
            else:
                result = await asyncio.to_thread(
                    openai_reply_with_usage,
                    prompt,
                    lang,
                    tools=APPOINTMENT_TOOLS,
                    tool_executor=async_tool_executor,
                    database_code=database_code,
                    salon_snapshot=snapshot
                )
        finally:
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
        if isinstance(result, dict) and 'text' in result:
            answer = result['text']
        else:
            answer = str(result)
        
        # Перевіряємо чи відповідь не є JSON-ом
        if answer and answer.strip().startswith('{') and answer.strip().endswith('}'):
            try:
                # Якщо це JSON, спробуємо витягти текст з нього
                json_data = json.loads(answer)
                if isinstance(json_data, dict):
                    # Шукаємо текстові поля в JSON
                    if 'text' in json_data:
                        answer = json_data['text']
                    elif 'message' in json_data:
                        answer = json_data['message']
                    elif 'response' in json_data:
                        answer = json_data['response']
                    else:
                        # Якщо не знайшли текстове поле, повертаємо помилку
                        lang = context.user_data.get("lang", "uk")
                        if lang == "uk":
                            answer = "Вибачте, отримав некоректну відповідь. Спробуйте переформулювати питання."
                        elif lang == "ru":
                            answer = "Извините, получил некорректный ответ. Попробуйте переформулировать вопрос."
                        else:
                            answer = "Sorry, received an incorrect response. Please try rephrasing your question."
            except json.JSONDecodeError:
                # Якщо не вдалося розпарсити JSON, залишаємо як є
                pass
    except Exception as e:
        answer = f"AI помилка: {e}"

    # Додаткова перевірка салону після AI відповіді (якщо ще не обрано)
    # Числівники вже оброблені на початку, тут тільки token matching
    if not context.user_data.get("SALON_FOLDER"):
        combined_text = incoming_text + " " + (answer or "")
        salon_match, score = resolve_salon_from_text(context.application, combined_text)
        if salon_match and score >= 0.8:
            folder = salon_match.get("folder")
            context.user_data["SALON_FOLDER"] = folder
            context.user_data["SALON_META"] = salon_match.get("meta")
            log.info(f"✅ Salon selected from conversation (post-AI): {folder} (score: {score})")
    
    # ТИМЧАСОВО ВИМКНЕНО: Перевірка та створення запису в CRM фоново
    # booking = context.user_data.get("booking")
    # if booking and all([booking.service_id, booking.master_id, booking.date, booking.time]):
    #     client_profile = context.user_data.get("client_profile", {})
    #     if client_profile.get("phone") and client_profile.get("email"):
    #         # Запускаємо фонове створення запису
    #         asyncio.create_task(create_appointment_background(
    #             update, context, booking, client_profile
    #         ))
    
    # відповідаємо користувачу
    # Telegram вимагає непорожній текст і обмеження 4096 символів
    safe_answer = (answer or "").strip()
    if not safe_answer:
        lang = context.user_data.get("lang", "en")
        if lang == "uk":
            safe_answer = "Вибачте, відповідь тимчасово недоступна. Спробуйте переформулювати питання."
        elif lang == "ru":
            safe_answer = "Извините, ответ временно недоступен. Попробуйте переформулировать вопрос."
        else:
            safe_answer = "Sorry, I couldn't generate a reply. Please try rephrasing."
    if len(safe_answer) > 4096:
        safe_answer = safe_answer[:4093] + "..."
    await update.message.reply_text(safe_answer)
    await log_bot_msg(update, context, answer)
    
    # Оновлюємо профіль тільки при важливих подіях (бронювання, контактні дані)
    should_update_profile = (is_booking_intent or 
                           "телефон" in incoming_text.lower() or 
                           "email" in incoming_text.lower() or 
                           "@" in incoming_text or 
                           any(char.isdigit() for char in incoming_text))
    
    if should_update_profile and recent_messages:
        try:
            # Оновлюємо пам'ять про клієнта
            db_user_id = context.user_data.get("db_user_id")
            if db_user_id:
                existing_memory = await asyncio.to_thread(get_conversation_memory, db_user_id)
                memory_update = await asyncio.to_thread(
                    extract_conversation_memory,
                    recent_messages,
                    existing_memory,
                )
                if memory_update:
                    await asyncio.to_thread(upsert_conversation_memory, db_user_id, memory_update)
            
            # Оновлюємо профіль клієнта
            user = update.effective_user
            if user:
                chat_id = str(user.id)
                new_profile_data = await asyncio.to_thread(extract_profile_from_context, recent_messages, lang)
                
                if new_profile_data:
                    existing_profile = await asyncio.to_thread(get_beauty_client, chat_id, "telegram")
                    merged_profile = merge_profile_data(existing_profile, new_profile_data)
                    
                    # Додаємо інформацію з контексту бота
                    if not merged_profile.get("language_code"):
                        merged_profile["language_code"] = lang
                    
                    salon_folder = context.user_data.get("SALON_FOLDER")
                    if salon_folder:
                        salon_meta = context.user_data.get("SALON_META", {})
                        salon_name = salon_meta.get("name") or salon_meta.get("nameLocation")
                        if salon_name:
                            if "favorite_salons" not in merged_profile:
                                merged_profile["favorite_salons"] = []
                            if salon_name not in merged_profile["favorite_salons"]:
                                merged_profile["favorite_salons"].append(salon_name)
                    
                    await asyncio.to_thread(upsert_beauty_client, chat_id, "telegram", merged_profile)
                    # Очищаємо кеш профіля
                    cache_key = f"client_profile_{chat_id}"
                    context.user_data.pop(cache_key, None)
                    context.user_data.pop(f"{cache_key}_time", None)
                    log.info(f"Updated beauty_client profile for chat_id={chat_id}")
        except Exception as exc:
            log.warning("Profile update failed: %s", exc)

    # Відповідь вже збережена в БД через log_bot_msg, не потрібно зберігати в context.user_data
    
    # зберігаємо контекст для наступних запитів
    if "жіноч" in q.lower() or "женск" in q.lower():
        context.user_data["service_gender"] = "female"
    elif "чоловіч" in q.lower() or "мужск" in q.lower():
        context.user_data["service_gender"] = "male"
    
    # Continue conversation - let AI handle the flow
    return FREE_QUESTION

async def handle_intent_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    if normalize(text) == normalize("Почати бронювання"):
        pre = context.user_data.get("PREFILL", {})
        if not pre:
            await update.message.reply_text("Не знайшов попередньо обрану послугу, перейдемо до списку.")
            return DECISION
        # ініціюємо бронювання з частково заповненими полями
        booking = Booking(group_name=pre.get("category"))
        booking.service_id = pre.get("service_id")
        booking.service_name = pre.get("service_name")
        context.user_data["booking"] = booking
        # далі — стандартний сценарій: вибір майстра/дати
        await update.message.reply_text("Добре, підготуємо запис. Оберіть майстра або дату.")
        return GROUP
    if normalize(text) == normalize("Ще питання"):
        await update.message.reply_text("Слухаю.")
        return FREE_QUESTION
    # Завершити
    await update.message.reply_text("Дякуємо за звернення!")
    return ConversationHandler.END

async def handle_autoconfirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Спрощений підтверджувач без прямих SQL-операцій (без Session/engine)."""
    await log_user_msg(update, context)
    text = (update.message.text or "").strip()
    if normalize(text) == normalize("Підтвердити"):
        await update.message.reply_text("Готово. Запис підтверджено. ✨")
        return ConversationHandler.END
    # Скасування
    await update.message.reply_text("Скасовано.")
    return ConversationHandler.END

def detect_language(text: str) -> str:
    """Визначає мову тексту"""
    text_lower = text.lower()
    # Українські слова-маркери
    uk_words = ["що", "як", "коли", "де", "чому", "який", "яка", "яке", "які", "можна", "треба", "хочу", "маю", "є", "буде", "було", "записатися", "запис", "салон", "майстер", "послуга", "ціна", "вартість"]
    # Російські слова-маркери
    ru_words = ["что", "как", "когда", "где", "почему", "какой", "какая", "какое", "какие", "можно", "нужно", "хочу", "есть", "будет", "было", "записаться", "запись", "салон", "мастер", "услуга", "цена", "стоимость"]
    # Англійські слова-маркери
    en_words = ["what", "how", "when", "where", "why", "which", "can", "want", "have", "book", "booking", "salon", "master", "service", "price", "cost"]
    
    uk_count = sum(1 for word in uk_words if word in text_lower)
    ru_count = sum(1 for word in ru_words if word in text_lower)
    en_count = sum(1 for word in en_words if word in text_lower)
    
    if uk_count > ru_count and uk_count > en_count:
        return "uk"
    elif ru_count > en_count:
        return "ru"
    elif en_count > 0:
        return "en"
    else:
        return "uk"  # за замовчуванням



async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка голосових повідомлень"""
    try:
        # Отримуємо голосове повідомлення
        voice = update.message.voice
        if not voice:
            return
        
        # Завантажуємо файл
        voice_file = await context.bot.get_file(voice.file_id)
        
        # Створюємо тимчасовий файл
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
            await voice_file.download_to_drive(temp_path)
        
        # Транскрибуємо аудіо
        try:
            transcribed_text = await asyncio.to_thread(transcribe_audio, temp_path)
        finally:
            # Видаляємо тимчасовий файл
            import os
            try:
                os.unlink(temp_path)
            except:
                pass
        
        if not transcribed_text.strip():
            lang = context.user_data.get("lang", "uk")
            if lang == "uk":
                reply = "Не вдалося розпізнати голосове повідомлення. Спробуйте ще раз або напишіть текстом."
            elif lang == "ru":
                reply = "Не удалось распознать голосовое сообщение. Попробуйте еще раз или напишите текстом."
            else:
                reply = "Could not recognize the voice message. Please try again or send a text message."
            await update.message.reply_text(reply)
            return
        
        # Логуємо розпізнаний текст
        await log_user_msg(update, context, f"[Голосове]: {transcribed_text}")
        
        # Створюємо новий контекст з розпізнаним текстом
        context.user_data["voice_transcribed_text"] = transcribed_text
        
        # Створюємо фейковий об'єкт повідомлення з текстом
        from types import SimpleNamespace
        fake_message = SimpleNamespace()
        fake_message.text = transcribed_text
        fake_message.from_user = update.message.from_user
        fake_message.chat = update.message.chat
        fake_message.message_id = update.message.message_id
        fake_message.date = update.message.date
        fake_message.reply_text = update.message.reply_text
        
        # Створюємо фейковий update
        fake_update = SimpleNamespace()
        fake_update.message = fake_message
        fake_update.effective_user = update.effective_user
        fake_update.effective_chat = update.effective_chat
        
        # Передаємо на обробку як звичайне текстове повідомлення
        return await handle_fallback_message(fake_update, context)
        
    except Exception as e:
        log.error(f"Error processing voice message: {e}")
        lang = context.user_data.get("lang", "uk")
        if lang == "uk":
            reply = "Помилка обробки голосового повідомлення. Спробуйте надіслати текстом."
        elif lang == "ru":
            reply = "Ошибка обработки голосового сообщения. Попробуйте отправить текстом."
        else:
            reply = "Error processing voice message. Please try sending a text message."
        await update.message.reply_text(reply)
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка фото та передача їх в AI-чат."""
    message = update.message
    if not message or not message.photo:
        return

    try:
        # Беремо найбільшу версію фото
        photo = message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        buffer = BytesIO()
        await photo_file.download_to_memory(out=buffer)
        encoded = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")

        lang = context.user_data.get("lang") or "uk"
        caption = (message.caption or "").strip()
        placeholders = {
            "uk": "Фото без підпису.",
            "ru": "Фото без подписи.",
            "en": "Photo without caption.",
        }
        text_for_ai = caption if caption else placeholders.get(lang, "Фото без підпису.")
        log_text = f"[Фото] {text_for_ai}".strip()

        context.user_data["pending_images"] = [encoded]
        context.user_data["pending_text_override"] = text_for_ai
        context.user_data["pending_log_override"] = log_text

        return await handle_fallback_message(update, context)
    except Exception as exc:
        log.error("Error processing photo message: %s", exc)
        lang = context.user_data.get("lang", "uk")
        if lang == "uk":
            reply = "Сталася помилка під час обробки фото. Спробуйте ще раз або опишіть запит текстом."
        elif lang == "ru":
            reply = "Произошла ошибка при обработке фото. Попробуйте еще раз или опишите запрос текстом."
        else:
            reply = "Something went wrong while processing the photo. Please try again or describe it with text."
        await message.reply_text(reply)

async def handle_fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback handler для AI чату поза conversation - просто перенаправляє на handle_free_question"""
    # Перевіряємо чи AI увімкнено
    db_user_id = context.user_data.get("db_user_id")
    if not db_user_id:
        db_user_id = await ensure_db_user(update, context)
    
    ai_enabled = await asyncio.to_thread(is_ai_enabled, db_user_id)
    if not ai_enabled:
        # AI вимкнено - логуємо та чекаємо відповіді адміна
        await log_user_msg(update, context)
        return
    
    # Викликаємо handle_free_question напряму
    return await handle_free_question(update, context)

# ------------------------ APP ----------------------------
def build_app() -> Application:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не знайдено у .env")

    # ініціалізуємо БД один раз на старті бота
    init_db()

    app = Application.builder().token(token).build()
    if app.job_queue:
        app.job_queue.run_repeating(tick_timers, interval=1.0, first=1.0)
    else:
        log.warning("JobQueue not available. Timer functionality will be disabled.")
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANGUAGE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_language), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            SALON:       [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_salon), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            GROUP:       [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            HAIR_LENGTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hair_length), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            SERVICE_PICK:[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_service_pick), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            MASTER_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_master_mode), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            MASTER_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_master_pick), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            MONTH:       [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_month), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            DAY:         [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_day), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            TIME_PICK:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time_pick), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],


            FREE_QUESTION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_question), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            INTENT_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_intent_decision), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
            AUTOCONFIRM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autoconfirm), MessageHandler(filters.VOICE, handle_voice_message), MessageHandler(filters.PHOTO, handle_photo_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="booking_i18n_localized_groups_services_paged",
        persistent=False,
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("reset", start))
    # Handlers для AI чату поза conversation
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_fallback_message))
    return app

def main():
    app = build_app()
    log.info("🤖 Bot is polling…")
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
