"""Офлайн-переклад назв послуг через словник + патерни.

Без API-викликів. Перекладає UK→RU/EN/PL токенізовано.
Запуск: python -m scripts.translate_dict [--country ua|pl|gb] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time

from sqlalchemy import select, update, text

from app.adapters.translations.service_translator import parse_multilingual_name, detect_source_lang, ALL_LANGS
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

# ── Рівні майстерності ─────────────────────────────────────────────
LEVELS = {
    "АРТ": {"ru": "АРТ", "en": "ART", "pl": "ART"},
    "МАЙСТЕР": {"ru": "МАСТЕР", "en": "MASTER", "pl": "MASTER"},
    "ТОП": {"ru": "ТОП", "en": "TOP", "pl": "TOP"},
    "БАРБЕР": {"ru": "БАРБЕР", "en": "BARBER", "pl": "BARBER"},
    "СОТРУДНИКИ": {"ru": "СОТРУДНИКИ", "en": "STAFF", "pl": "PRACOWNICY"},
}

# ── Довжина ─────────────────────────────────────────────────────────
# "1 довжина" / "2 довжина" etc → "length 1" / "длина 1" / "długość 1"
LENGTH_RE = re.compile(r'(\d+)\s*(?:дов?жина|довж\.?)', re.IGNORECASE)
LENGTH_RANGE_RE = re.compile(r'(\d+)[/-](\d+)\s*(?:дов?жина|довж\.?)', re.IGNORECASE)
# "довжина 1" pattern
LENGTH_ALT_RE = re.compile(r'(?:дов?жина|довж\.?)\s*(\d+)', re.IGNORECASE)

# ── Фрази (longest match first) ────────────────────────────────────
# Format: uk → (ru, en, pl)
PHRASES: dict[str, tuple[str, str, str]] = {
    # ── Час ──
    "1 година": ("1 час", "1 hour", "1 godzina"),
    "2 години": ("2 часа", "2 hours", "2 godziny"),
    "30 хвилин": ("30 минут", "30 minutes", "30 minut"),
    "хв.": ("мин.", "min.", "min."),
    "хв": ("мин", "min", "min"),

    # ── Волосся загальне ──
    "стрижка чоловіча": ("мужская стрижка", "men's haircut", "strzyżenie męskie"),
    "стрижка жіноча": ("женская стрижка", "women's haircut", "strzyżenie damskie"),
    "стрижка дитяча": ("детская стрижка", "children's haircut", "strzyżenie dziecięce"),
    "стрижка чубчика": ("стрижка чёлки", "bangs trim", "strzyżenie grzywki"),
    "стрижка кінчиків": ("стрижка кончиков", "ends trim", "podcinanie końcówek"),
    "стрижка гарячими ножицями": ("стрижка горячими ножницами", "hot scissors haircut", "strzyżenie gorącymi nożyczkami"),
    "стрижка машинкою": ("стрижка машинкой", "clipper haircut", "strzyżenie maszynką"),
    "стрижка": ("стрижка", "haircut", "strzyżenie"),
    "укладка на гарячий інструмент": ("укладка на горячий инструмент", "hot tool styling", "stylizacja na gorące narzędzia"),
    "укладання на гарячий інструмент": ("укладка на горячий инструмент", "hot tool styling", "stylizacja na gorące narzędzia"),
    "укладання на Люкс косметиці": ("укладка на Люкс косметике", "luxury cosmetics styling", "stylizacja na kosmetykach luksusowych"),
    "укладання": ("укладка", "styling", "stylizacja"),
    "укладка на гарячий інструмент на Люкс косметиці": ("укладка на горячий инструмент на Люкс косметике", "luxury hot tool styling", "stylizacja na gorące narzędzia luksusowe"),
    "укладка чоловіча": ("мужская укладка", "men's styling", "stylizacja męska"),
    "укладка": ("укладка", "styling", "stylizacja"),
    "зачіска": ("причёска", "hairstyle", "fryzura"),
    "весільна зачіска": ("свадебная причёска", "wedding hairstyle", "fryzura ślubna"),
    "весільна зачіска/зачіска для особливого приводу": ("свадебная причёска/причёска для особого повода", "wedding/special occasion hairstyle", "fryzura ślubna/na specjalną okazję"),
    "плетіння": ("плетение", "braiding", "plecenie"),
    "афронакрутка": ("афронакрутка", "afro curling", "kręcenie afro"),
    "афронакрутка (афрокучері)": ("афронакрутка (афрокудри)", "afro curling (afro curls)", "kręcenie afro (loki afro)"),
    "афро кудрі": ("афро кудри", "afro curls", "loki afro"),
    "афрокудрі": ("афрокудри", "afro curls", "loki afro"),
    "біозавивка": ("биозавивка", "bio perm", "trwała biologiczna"),

    # ── Фарбування ──
    "фарбування коренів": ("окрашивание корней", "root coloring", "farbowanie odrostów"),
    "фарбування чоловіче": ("мужское окрашивание", "men's coloring", "farbowanie męskie"),
    "фарбування": ("окрашивание", "coloring", "farbowanie"),
    "преміум фарбування": ("премиум окрашивание", "premium coloring", "farbowanie premium"),
    "безаміачне тонування": ("безаммиачное тонирование", "ammonia-free toning", "tonowanie bezamoniakowe"),
    "тонування": ("тонирование", "toning", "tonowanie"),
    "мелірування": ("мелирование", "highlights", "rozjaśnianie"),
    "мелирування": ("мелирование", "highlights", "rozjaśnianie"),
    "освітлюючі масла": ("осветляющие масла", "lightening oils", "olejki rozjaśniające"),
    "зняття кольору кислотне": ("кислотная смывка цвета", "acid color removal", "kwasowe usuwanie koloru"),
    "зняття кольору пудрою": ("снятие цвета пудрой", "powder color removal", "usuwanie koloru pudrem"),
    "зняття кольору": ("снятие цвета", "color removal", "usuwanie koloru"),
    "блонд миття": ("блонд мытьё", "blonde wash", "mycie blond"),
    "блонд-миття": ("блонд-мытьё", "blonde wash", "mycie blond"),
    "вихід із чорного": ("выход из чёрного", "transition from black", "wychodzenie z czarnego"),
    "вихід з чорного": ("выход из чёрного", "transition from black", "wychodzenie z czarnego"),
    "висвітлення": ("осветление", "lightening", "rozjaśnianie"),
    "освітлення": ("осветление", "lightening", "rozjaśnianie"),
    "анімація кольору": ("анимация цвета", "color animation", "animacja koloru"),
    "анімація кольору складна": ("сложная анимация цвета", "complex color animation", "złożona animacja koloru"),
    "камуфлювання": ("камуфлирование", "camouflage coloring", "kamuflaż"),
    "відтінок": ("оттенок", "tint", "odcień"),

    # ── Манікюр / педикюр ──
    "манікюр чоловічий": ("мужской маникюр", "men's manicure", "manicure męski"),
    "японський манікюр чоловічий": ("японский мужской маникюр", "Japanese men's manicure", "manicure japoński męski"),
    "японський манікюр": ("японский маникюр", "Japanese manicure", "manicure japoński"),
    "класичний манікюр": ("классический маникюр", "classic manicure", "manicure klasyczny"),
    "манікюр": ("маникюр", "manicure", "manicure"),
    "педикюр чоловічий": ("мужской педикюр", "men's pedicure", "pedicure męski"),
    "японський педикюр чоловічий": ("японский мужской педикюр", "Japanese men's pedicure", "pedicure japoński męski"),
    "японський педикюр": ("японский педикюр", "Japanese pedicure", "pedicure japoński"),
    "класичний педикюр": ("классический педикюр", "classic pedicure", "pedicure klasyczny"),
    "частковий педикюр стопа": ("частичный педикюр стопы", "partial foot pedicure", "pedicure częściowy stóp"),
    "педикюр пальці з подальшим покриттям": ("педикюр пальцев с покрытием", "toe pedicure with coating", "pedicure palców z lakierem"),
    "педикюр пальці без покриття": ("педикюр пальцев без покрытия", "toe pedicure without coating", "pedicure palców bez lakieru"),
    "педикюр": ("педикюр", "pedicure", "pedicure"),
    "гель-лак дизайн френч": ("гель-лак дизайн/френч", "gel polish design/french", "żel-lakier design/french"),
    "гель-лак дизайн/френч": ("гель-лак дизайн/френч", "gel polish design/french", "żel-lakier design/french"),
    "гель-лак дизайн": ("гель-лак дизайн", "gel polish design", "żel-lakier design"),
    "гель-лак": ("гель-лак", "gel polish", "żel-lakier"),
    "зняття гель-лаку": ("снятие гель-лака", "gel polish removal", "zdejmowanie żel-lakieru"),
    "зняття лаку": ("снятие лака", "polish removal", "zdejmowanie lakieru"),
    "зняття покриття": ("снятие покрытия", "coating removal", "zdejmowanie powłoki"),
    "лак": ("лак", "nail polish", "lakier"),
    "укріплення акригелем": ("укрепление акригелем", "acrylic gel reinforcement", "wzmocnienie żelem akrylowym"),
    "укріплення": ("укрепление", "reinforcement", "wzmocnienie"),
    "видалення оніхолізису": ("удаление онихолизиса", "onycholysis removal", "usuwanie onycholizy"),
    "протезування нігтя": ("протезирование ногтя", "nail prosthetics", "protezowanie paznokcia"),
    "нарощування": ("наращивание", "extensions", "przedłużanie"),
    "нарощування нігтів": ("наращивание ногтей", "nail extensions", "przedłużanie paznokci"),
    "викладний френч": ("выкладной френч", "sculpted french", "french modelowany"),
    "френч": ("френч", "french", "french"),
    "втирка": ("втирка", "nail rub", "wcierka"),
    "видалення врослого нігтя": ("удаление вросшего ногтя", "ingrown nail removal", "usuwanie wrastającego paznokcia"),
    "видалення врослого сегмента": ("удаление вросшего сегмента", "ingrown segment removal", "usuwanie wrastającego segmentu"),
    "врослий ніготь": ("вросший ноготь", "ingrown nail", "wrastający paznokieć"),
    "відновлення архітектури нігтів": ("восстановление архитектуры ногтей", "nail architecture restoration", "odbudowa architektury paznokci"),
    "відновлення архутектури нігтів": ("восстановление архитектуры ногтей", "nail architecture restoration", "odbudowa architektury paznokci"),

    # ── Брови / вії ──
    "корекція брів з воском": ("коррекция бровей воском", "eyebrow wax correction", "korekta brwi woskiem"),
    "корекція брів": ("коррекция бровей", "eyebrow correction", "korekta brwi"),
    "моделювання брів": ("моделирование бровей", "eyebrow shaping", "modelowanie brwi"),
    "фарбування брів": ("окрашивание бровей", "eyebrow coloring", "farbowanie brwi"),
    "фарбування + корекція брів": ("окрашивание + коррекция бровей", "eyebrow coloring + correction", "farbowanie + korekta brwi"),
    "ламінування брів": ("ламинирование бровей", "eyebrow lamination", "laminowanie brwi"),
    "біофіксація брів": ("биофиксация бровей", "eyebrow biofixation", "biofiksacja brwi"),
    "висвітлення брів": ("осветление бровей", "eyebrow lightening", "rozjaśnianie brwi"),
    "корекція брів чоловіча": ("мужская коррекция бровей", "men's eyebrow correction", "korekta brwi męska"),
    "фарбування вій": ("окрашивание ресниц", "eyelash coloring", "farbowanie rzęs"),
    "ламінування вій": ("ламинирование ресниц", "eyelash lamination", "laminowanie rzęs"),
    "ламінування вій + відновлення": ("ламинирование ресниц + восстановление", "eyelash lamination + restoration", "laminowanie rzęs + regeneracja"),
    "ботокс для брів": ("ботокс для бровей", "eyebrow botox", "botox na brwi"),
    "ботокс для вій": ("ботокс для ресниц", "eyelash botox", "botox na rzęsy"),
    "ботокс брови/вії": ("ботокс брови/ресницы", "eyebrow/eyelash botox", "botox brwi/rzęsy"),
    "ботокс брови/віі": ("ботокс брови/ресницы", "eyebrow/eyelash botox", "botox brwi/rzęsy"),
    "відновлення для вій або брів": ("восстановление для ресниц или бровей", "eyelash or eyebrow restoration", "regeneracja rzęs lub brwi"),
    "відновлення для вій / брів": ("восстановление для ресниц / бровей", "eyelash / eyebrow restoration", "regeneracja rzęs / brwi"),
    "вії стрічки": ("ресницы ленты", "strip lashes", "rzęsy taśmowe"),
    "вії штучні (частково)": ("ресницы искусственные (частично)", "partial false lashes", "rzęsy sztuczne (częściowe)"),
    "вії штучні": ("ресницы искусственные", "false lashes", "rzęsy sztuczne"),
    "вигин кольоровий": ("цветной изгиб", "colored curl", "kolorowy zawijanie"),
    "вигин L, M": ("изгиб L, M", "curl L, M", "zawijanie L, M"),
    "вигин М, L": ("изгиб M, L", "curl M, L", "zawijanie M, L"),

    # ── Макіяж ──
    "денний макіяж": ("дневной макияж", "day makeup", "makijaż dzienny"),
    "коктейльний макіяж": ("коктейльный макияж", "cocktail makeup", "makijaż koktajlowy"),
    "вечірній макіяж": ("вечерний макияж", "evening makeup", "makijaż wieczorowy"),
    "весільний макіяж": ("свадебный макияж", "wedding makeup", "makijaż ślubny"),
    "макіяж для особливих випадків": ("макияж для особых случаев", "special occasion makeup", "makijaż na specjalne okazje"),
    "базовий урок макіяжу для себе": ("базовый урок макияжа для себя", "basic self-makeup lesson", "podstawowa lekcja makijażu"),
    "макіяж": ("макияж", "makeup", "makijaż"),
    "перманентний макіяж губ": ("перманентный макияж губ", "permanent lip makeup", "makijaż permanentny ust"),
    "перманентний макіяж брів": ("перманентный макияж бровей", "permanent eyebrow makeup", "makijaż permanentny brwi"),
    "корекція перманентного макіяжу губ": ("коррекция перманентного макияжа губ", "permanent lip makeup correction", "korekta makijażu permanentnego ust"),
    "корекція перманентного макіяжу брів": ("коррекция перманентного макияжа бровей", "permanent eyebrow makeup correction", "korekta makijażu permanentnego brwi"),

    # ── Депіляція / епіляція ──
    "депіляція лазер": ("лазерная депиляция", "laser depilation", "depilacja laserowa"),
    "депіляція": ("депиляция", "depilation", "depilacja"),
    "воскова епіляція глибоке бікіні": ("восковая эпиляция глубокое бикини", "deep bikini waxing", "depilacja woskowa głębokie bikini"),
    "воскова епіляція класичне бікіні": ("восковая эпиляция классическое бикини", "classic bikini waxing", "depilacja woskowa klasyczne bikini"),
    "воскова епіляція": ("восковая эпиляция", "waxing", "depilacja woskowa"),
    "бікіні глибоке": ("глубокое бикини", "deep bikini", "głębokie bikini"),
    "бікіні": ("бикини", "bikini", "bikini"),

    # ── Масаж ──
    "антицелюлітний масаж": ("антицеллюлитный массаж", "anti-cellulite massage", "masaż antycellulitowy"),
    "антицилюлітний масаж": ("антицеллюлитный массаж", "anti-cellulite massage", "masaż antycellulitowy"),
    "антицелюлітний": ("антицеллюлитный", "anti-cellulite", "antycellulitowy"),
    "антицилюлітний": ("антицеллюлитный", "anti-cellulite", "antycellulitowy"),
    "букальний масаж": ("буккальный массаж", "buccal massage", "masaż bukkalny"),
    "відновлюючий масаж спини": ("восстанавливающий массаж спины", "restorative back massage", "masaż regenerujący pleców"),
    "авторський масаж": ("авторский массаж", "signature massage", "masaż autorski"),
    "масаж": ("массаж", "massage", "masaż"),

    # ── Догляд за обличчям ──
    "атравматична чистка обличчя": ("атравматическая чистка лица", "atraumatic facial cleansing", "oczyszczanie atraumatyczne twarzy"),
    "атравматична чистка шкіри всіх типів": ("атравматическая чистка кожи всех типов", "atraumatic cleansing for all skin types", "oczyszczanie atraumatyczne dla wszystkich typów skóry"),
    "атравматична чистка": ("атравматическая чистка", "atraumatic cleansing", "oczyszczanie atraumatyczne"),
    "класична чистка": ("классическая чистка", "classic cleansing", "oczyszczanie klasyczne"),
    "чистка": ("чистка", "cleansing", "oczyszczanie"),
    "альгінатна маска": ("альгинатная маска", "alginate mask", "maska alginatowa"),
    "безголкова мезотерапія": ("безигольная мезотерапия", "needle-free mesotherapy", "mezoterapia bezigłowa"),
    "мезотерапія": ("мезотерапия", "mesotherapy", "mezoterapia"),
    "гальванізація": ("гальванизация", "galvanization", "galwanizacja"),
    "мікротоки": ("микротоки", "microcurrents", "mikroprądy"),
    "мікротокова терапія": ("микротоковая терапия", "microcurrent therapy", "terapia mikropradowa"),
    "пілінг шкіри голови": ("пилинг кожи головы", "scalp peeling", "peeling skóry głowy"),
    "пілінг": ("пилинг", "peeling", "peeling"),
    "карбокситерапія": ("карбокситерапия", "carboxytherapy", "karboksyterapia"),
    "карбокситерапія обличчя / шия / декольте апаратна": ("аппаратная карбокситерапия лица/шеи/декольте", "hardware carboxytherapy face/neck/décolleté", "karboksyterapia aparaturowa twarz/szyja/dekolt"),

    # ── Ботокс / ін'єкції ──
    "ботокс верхня губа": ("ботокс верхняя губа", "upper lip botox", "botox górna warga"),
    "ботокс лоб + міжбрів'я + очі": ("ботокс лоб + межбровье + глаза", "botox forehead + glabella + eyes", "botox czoło + międzybrwiowe + oczy"),
    "ботокс лоб + міжбрів'я": ("ботокс лоб + межбровье", "botox forehead + glabella", "botox czoło + międzybrwiowe"),
    "ботокс лоб": ("ботокс лоб", "forehead botox", "botox czoło"),
    "ботокс міжбрівка": ("ботокс межбровье", "glabella botox", "botox międzybrwiowe"),
    "ботокс міжбрів'я": ("ботокс межбровье", "glabella botox", "botox międzybrwiowe"),
    "ботокс нижньої третини": ("ботокс нижней трети", "lower third botox", "botox dolna trzecia"),
    "ботокс зона навколо очей": ("ботокс зона вокруг глаз", "eye area botox", "botox okolice oczu"),
    "ботокс зона очей (гусячі лапки)": ("ботокс зона глаз (гусиные лапки)", "eye area botox (crow's feet)", "botox okolice oczu (kurze łapki)"),
    "ботокс шия Нефертіті": ("ботокс шея Нефертити", "Nefertiti neck botox", "botox szyja Nefertiti"),
    "ботокс для волосся гарячий": ("горячий ботокс для волос", "hot hair botox", "gorący botox na włosy"),
    "ботокс для волосся": ("ботокс для волос", "hair botox", "botox na włosy"),
    "ботокс": ("ботокс", "botox", "botox"),
    "ботулінотерапія": ("ботулинотерапия", "botulinum therapy", "terapia botulinowa"),
    "біоревіталізація": ("биоревитализация", "biorevitalization", "biowitalizacja"),
    "биоревитализация": ("биоревитализация", "biorevitalization", "biowitalizacja"),
    "біорепарація": ("биорепарация", "bioreparation", "bioreparacja"),
    "біоревіталізант": ("биоревитализант", "biorevitalizant", "biowitalizant"),
    "контурна пластика": ("контурная пластика", "contour plastic", "plastyka konturowa"),
    "колагеностимулятор": ("коллагеностимулятор", "collagen stimulator", "stymulator kolagenu"),
    "філлер для волосся": ("филлер для волос", "hair filler", "filler do włosów"),
    "філер": ("филлер", "filler", "filler"),
    "ліполітики": ("липолитики", "lipolytics", "lipolityki"),
    "анестезія аплікаторна": ("аппликаторная анестезия", "applicator anesthesia", "znieczulenie aplikacyjne"),
    "анестезія ін'єкційна": ("инъекционная анестезия", "injection anesthesia", "znieczulenie iniekcyjne"),
    "бланшинг": ("бланширование", "blanching", "blanching"),

    # ── RF / SMAS / апаратні ──
    "RF-ліфтинг": ("RF-лифтинг", "RF-lifting", "RF-lifting"),
    "SMAS-ліфтинг": ("SMAS-лифтинг", "SMAS-lifting", "SMAS-lifting"),
    "SMAS-ліфтінг": ("SMAS-лифтинг", "SMAS-lifting", "SMAS-lifting"),

    # ── Догляд за волоссям ──
    "лікування волосся": ("лечение волос", "hair treatment", "leczenie włosów"),
    "лікування": ("лечение", "treatment", "leczenie"),
    "реконструкція волосся": ("реконструкция волос", "hair reconstruction", "rekonstrukcja włosów"),
    "реконструкція": ("реконструкция", "reconstruction", "rekonstrukcja"),
    "відновлення волосся": ("восстановление волос", "hair restoration", "regeneracja włosów"),
    "відновлення та зміцнення": ("восстановление и укрепление", "restoration and strengthening", "regeneracja i wzmocnienie"),
    "відновлення": ("восстановление", "restoration", "regeneracja"),
    "відновлювальна процедура": ("восстанавливающая процедура", "restorative procedure", "zabieg regenerujący"),
    "процедура відновлення": ("процедура восстановления", "restoration procedure", "zabieg regeneracji"),
    "процедура відновлення РН": ("процедура восстановления pH", "pH restoration procedure", "zabieg przywracania pH"),
    "ампульний догляд": ("ампульный уход", "ampoule treatment", "zabieg ampułkowy"),
    "ампула миттєвої краси": ("ампула мгновенной красоты", "instant beauty ampoule", "ampułka natychmiastowej urody"),
    "ампула відновлення": ("ампула восстановления", "restoration ampoule", "ampułka regeneracji"),
    "ампула": ("ампула", "ampoule", "ampułka"),
    "ампули": ("ампулы", "ampoules", "ampułki"),
    "маска": ("маска", "mask", "maska"),
    "експрес маска": ("экспресс маска", "express mask", "maseczka ekspresowa"),
    "експрес-лікування волосся": ("экспресс-лечение волос", "express hair treatment", "ekspresowe leczenie włosów"),
    "експрес догляд за волосям": ("экспресс уход за волосами", "express hair care", "ekspresowa pielęgnacja włosów"),
    "обгортання маслом": ("обёртывание маслом", "oil wrapping", "owijanie olejkiem"),
    "детокс шкіри голови": ("детокс кожи головы", "scalp detox", "detoks skóry głowy"),
    "кератин": ("кератин", "keratin", "keratyna"),
    "кератинове випрямлення": ("кератиновое выпрямление", "keratin straightening", "keratynowe prostowanie"),
    "ламінування": ("ламинирование", "lamination", "laminowanie"),
    "вітоламінірування": ("витоламинирование", "vitalamination", "witolaminowanie"),
    "глибоке зволоженя": ("глубокое увлажнение", "deep moisturizing", "głębokie nawilżanie"),
    "глибоке зволоження": ("глубокое увлажнение", "deep moisturizing", "głębokie nawilżanie"),
    "живлення": ("питание", "nourishment", "odżywienie"),
    "зволоження": ("увлажнение", "moisturizing", "nawilżanie"),
    "захист при фарбуванні": ("защита при окрашивании", "protection during coloring", "ochrona podczas farbowania"),
    "захист волосся під час фарбування та освітлення": ("защита волос при окрашивании и осветлении", "hair protection during coloring and lightening", "ochrona włosów podczas farbowania i rozjaśniania"),
    "захист без уповільнення освітлення": ("защита без замедления осветления", "protection without lightening delay", "ochrona bez opóźniania rozjaśniania"),
    "активний захист": ("активная защита", "active protection", "aktywna ochrona"),
    "базовий захист": ("базовая защита", "basic protection", "podstawowa ochrona"),
    "захист": ("защита", "protection", "ochrona"),
    "догляд при фарбуванні": ("уход при окрашивании", "care during coloring", "pielęgnacja podczas farbowania"),
    "проти випадіння (базова)": ("против выпадения (базовая)", "anti-hair loss (basic)", "przeciw wypadaniu (podstawowy)"),
    "проти випадіння (поглиблена)": ("против выпадения (углублённая)", "anti-hair loss (advanced)", "przeciw wypadaniu (zaawansowany)"),
    "проти випадіння": ("против выпадения", "anti-hair loss", "przeciw wypadaniu"),
    "проти жирної лупи": ("против жирной перхоти", "anti-oily dandruff", "przeciw tłustemu łupieżowi"),
    "проти сухої лупи": ("против сухой перхоти", "anti-dry dandruff", "przeciw suchemu łupieżowi"),
    "антибактеріальна (проти сухої лупи)": ("антибактериальная (против сухой перхоти)", "antibacterial (anti-dry dandruff)", "antybakteryjna (przeciw suchemu łupieżowi)"),
    "регенерація та стимуляція росту волосся": ("регенерация и стимуляция роста волос", "regeneration and hair growth stimulation", "regeneracja i stymulacja wzrostu włosów"),
    "скраб": ("скраб", "scrub", "peeling"),
    "коктейль": ("коктейль", "cocktail", "koktajl"),

    # ── SPA ──
    "SPA-чистка": ("SPA-чистка", "SPA cleansing", "SPA oczyszczanie"),
    "SPA процедура": ("SPA процедура", "SPA procedure", "zabieg SPA"),
    "SPA-процедура": ("SPA-процедура", "SPA procedure", "zabieg SPA"),
    "SPA-ПРОЦЕДУРА": ("SPA-ПРОЦЕДУРА", "SPA PROCEDURE", "ZABIEG SPA"),
    "SPA догляд": ("SPA уход", "SPA care", "pielęgnacja SPA"),
    "SPA-догляд": ("SPA-уход", "SPA care", "pielęgnacja SPA"),
    "SPA-рукавички": ("SPA-перчатки", "SPA gloves", "rękawiczki SPA"),
    "SPA скраб": ("SPA скраб", "SPA scrub", "SPA peeling"),
    "SPA процедура Щастя для брів": ("SPA процедура Счастье для бровей", "SPA Happiness for eyebrows", "SPA Szczęście dla brwi"),
    "SPA \"Щастя для брів\"": ("SPA \"Счастье для бровей\"", "SPA \"Happiness for eyebrows\"", "SPA \"Szczęście dla brwi\""),
    "SPA для брів": ("SPA для бровей", "SPA for eyebrows", "SPA dla brwi"),
    "SPA послуга \"СВІЖА ХОДА\"": ("SPA услуга \"СВЕЖАЯ ПОХОДКА\"", "SPA service \"FRESH WALK\"", "usługa SPA \"ŚWIEŻY KROK\""),
    "процедура": ("процедура", "procedure", "zabieg"),

    # ── Для ніг / рук ──
    "для ніг": ("для ног", "for feet", "dla stóp"),
    "для рук": ("для рук", "for hands", "dla dłoni"),
    "для рук/ніг": ("для рук/ног", "for hands/feet", "dla dłoni/stóp"),
    "ноги": ("ноги", "legs", "nogi"),
    "руки": ("руки", "hands", "dłonie"),
    "скраб + маска": ("скраб + маска", "scrub + mask", "peeling + maska"),
    "cкраб+маска": ("скраб+маска", "scrub+mask", "peeling+maska"),
    "скраб + маска+ лосьйон": ("скраб + маска + лосьон", "scrub + mask + lotion", "peeling + maska + balsam"),
    "холодна парафінотерапія": ("холодная парафинотерапия", "cold paraffin therapy", "zimna parafinterapia"),
    "парафін": ("парафин", "paraffin", "parafina"),
    "свічка": ("свечка", "candle", "świeczka"),
    "свічка + масаж": ("свечка + массаж", "candle + massage", "świeczka + masaż"),
    "носочки": ("носочки", "socks", "skarpetki"),

    # ── Тіло ──
    "бедра": ("бёдра", "thighs", "uda"),
    "бедра (внутрішня поверхня)": ("бёдра (внутренняя поверхность)", "thighs (inner surface)", "uda (wewnętrzna powierzchnia)"),
    "бедра (задня поверхня)": ("бёдра (задняя поверхность)", "thighs (back surface)", "uda (tylna powierzchnia)"),
    "бедра (передня поверхня)": ("бёдра (передняя поверхность)", "thighs (front surface)", "uda (przednia powierzchnia)"),
    "бедра повністю": ("бёдра полностью", "full thighs", "uda w całości"),
    "сідниці": ("ягодицы", "buttocks", "pośladki"),
    "живіт": ("живот", "abdomen", "brzuch"),
    "живіт (повністю)": ("живот (полностью)", "full abdomen", "brzuch (w całości)"),
    "живіт+боки": ("живот+бока", "abdomen+flanks", "brzuch+boki"),
    "боки": ("бока", "flanks", "boki"),
    "ніжки повністю": ("ноги полностью", "full legs", "nogi w całości"),
    "руки повністю": ("руки полностью", "full arms", "ramiona w całości"),
    "руки (трицепс)": ("руки (трицепс)", "arms (triceps)", "ramiona (triceps)"),
    "спина повністю": ("спина полностью", "full back", "plecy w całości"),
    "спина (поперек)": ("спина (поясница)", "lower back", "plecy (lędźwie)"),
    "гомілки": ("голени", "shins", "łydki"),
    "талія": ("талия", "waist", "talia"),
    "все тіло": ("всё тело", "full body", "całe ciało"),
    "декольте": ("декольте", "décolleté", "dekolt"),
    "обличчя": ("лицо", "face", "twarz"),
    "обличча": ("лицо", "face", "twarz"),
    "шия": ("шея", "neck", "szyja"),
    "очей": ("глаз", "eyes", "oczu"),

    # ── Зони ──
    "1 зона": ("1 зона", "1 zone", "1 strefa"),
    "1 зони обличчя": ("1 зоны лица", "1 face zone", "1 strefy twarzy"),
    "верхня губа": ("верхняя губа", "upper lip", "górna warga"),
    "підборіддя": ("подбородок", "chin", "podbródek"),
    "нижня третина": ("нижняя треть", "lower third", "dolna trzecia"),
    "вилиці": ("скулы", "cheekbones", "kości policzkowe"),
    "пальці": ("пальцы", "fingers", "palce"),
    "ареоли": ("ареолы", "areolas", "areole"),
    "ареоли молочних залоз": ("ареолы молочных желёз", "breast areolas", "areole piersiowe"),
    "біла лінія живота": ("белая линия живота", "linea alba", "linia biała brzucha"),
    "внутрішня або задня поверхня стегна": ("внутренняя или задняя поверхность бедра", "inner or back thigh surface", "wewnętrzna lub tylna powierzchnia uda"),
    "локальні жирові відкладення": ("локальные жировые отложения", "local fat deposits", "lokalne złogi tłuszczu"),

    # ── Подологія ──
    "видалення бородавки": ("удаление бородавки", "wart removal", "usuwanie brodawki"),
    "видалення бородавок": ("удаление бородавок", "warts removal", "usuwanie brodawek"),
    "видалення великої бородавки": ("удаление большой бородавки", "large wart removal", "usuwanie dużej brodawki"),
    "видалення зони бородавок + розвантаження": ("удаление зоны бородавок + разгрузка", "wart zone removal + offloading", "usuwanie strefy brodawek + odciążenie"),
    "бородавка": ("бородавка", "wart", "brodawka"),
    "видалення камедону": ("удаление камедона", "comedone removal", "usuwanie zaskórnika"),
    "видалення комедона": ("удаление комедона", "comedone removal", "usuwanie zaskórnika"),
    "видалення комедону": ("удаление комедона", "comedone removal", "usuwanie zaskórnika"),
    "видалення мозоля": ("удаление мозоли", "callus removal", "usuwanie odcisku"),
    "видалення натоптишу": ("удаление натоптыша", "corn removal", "usuwanie nagniotka"),
    "видалення піднігтьового мозолю": ("удаление подногтевой мозоли", "subungual callus removal", "usuwanie odcisku podpaznokciowego"),
    "видалення піднігтьового мозоля": ("удаление подногтевой мозоли", "subungual callus removal", "usuwanie odcisku podpaznokciowego"),
    "видалення піднігтьової мазолі": ("удаление подногтевой мозоли", "subungual callus removal", "usuwanie odcisku podpaznokciowego"),
    "видалення кожного наступного мозолю": ("удаление каждой следующей мозоли", "each next callus removal", "usuwanie każdego następnego odcisku"),
    "видалення кожного наступного піднігтьового мозоля": ("удаление каждой следующей подногтевой мозоли", "each next subungual callus removal", "usuwanie każdego następnego odcisku podpaznokciowego"),
    "видалення кожноі наступноі бородавки": ("удаление каждой следующей бородавки", "each next wart removal", "usuwanie każdej następnej brodawki"),
    "видалення кожної наступної бородавки": ("удаление каждой следующей бородавки", "each next wart removal", "usuwanie każdej następnej brodawki"),
    "видалення стержневого мозоля": ("удаление стержневой мозоли", "core callus removal", "usuwanie odcisku głębokiego"),
    "видалення міліумів поодиноких": ("удаление единичных милиумов", "single milium removal", "usuwanie pojedynczych prosków"),
    "видалення папіломи": ("удаление папилломы", "papilloma removal", "usuwanie brodawczaka"),
    "видалення елементу": ("удаление элемента", "element removal", "usuwanie elementu"),
    "видалення елементів": ("удаление элементов", "elements removal", "usuwanie elementów"),
    "встановлення титанової нитки": ("установка титановой нити", "titanium thread installation", "zakładanie nici tytanowej"),
    "встановлення титановоі нитки": ("установка титановой нити", "titanium thread installation", "zakładanie nici tytanowej"),
    "встановлення пластини": ("установка пластины", "plate installation", "zakładanie płytki"),
    "встановлення скоби": ("установка скобы", "staple installation", "zakładanie klamry"),
    "встановлення корекційної системи": ("установка коррекционной системы", "correction system installation", "zakładanie systemu korekcyjnego"),
    "виготовлення індивідуальних ортезів": ("изготовление индивидуальных ортезов", "custom orthoses manufacturing", "produkcja indywidualnych ortez"),
    "виготовлення індивідуального ортоза": ("изготовление индивидуального ортеза", "custom orthosis manufacturing", "produkcja indywidualnej ortezy"),
    "виготовлення індивідуального розвантаження": ("изготовление индивидуальной разгрузки", "custom offloading manufacturing", "produkcja indywidualnego odciążenia"),
    "виготовлення індивідуального розвантяження": ("изготовление индивидуальной разгрузки", "custom offloading manufacturing", "produkcja indywidualnego odciążenia"),
    "тампонадою": ("тампонадой", "with tamponade", "z tamponadą"),
    "тампонада": ("тампонада", "tamponade", "tamponada"),
    "антигрибкова обробка нігтя (профілактика)": ("противогрибковая обработка ногтя (профилактика)", "antifungal nail treatment (preventive)", "zabieg przeciwgrzybiczy paznokcia (profilaktyka)"),

    # ── Загальні модифікатори ──
    "(додатково)": ("(дополнительно)", "(additionally)", "(dodatkowo)"),
    "додатково": ("дополнительно", "additionally", "dodatkowo"),
    "чоловіче": ("мужское", "men's", "męskie"),
    "чоловічий": ("мужской", "men's", "męski"),
    "чоловіча": ("мужская", "women's", "męska"),
    "чоловікам": ("мужчинам", "for men", "dla mężczyzn"),
    "глибокий": ("глубокий", "deep", "głęboki"),
    "легкий": ("легкий", "light", "lekki"),
    "середній": ("средний", "medium", "średni"),
    "складна": ("сложная", "complex", "złożony"),
    "складність": ("сложность", "complexity", "złożoność"),
    "1 складність": ("1 сложность", "complexity 1", "złożoność 1"),
    "2 складність": ("2 сложность", "complexity 2", "złożoność 2"),
    "первинний": ("первичный", "primary", "pierwotny"),
    "повторний": ("повторный", "repeated", "powtórny"),
    "великий": ("большой", "large", "duży"),
    "малий": ("малый", "small", "mały"),
    "без анестезії": ("без анестезии", "without anesthesia", "bez znieczulenia"),
    "з анестезією": ("с анестезией", "with anesthesia", "ze znieczuleniem"),
    "з анестезії": ("с анестезией", "with anesthesia", "ze znieczuleniem"),
    "до процедури догляду": ("до процедуры ухода", "before care procedure", "przed zabiegiem pielęgnacyjnym"),
    "до фарбування": ("до окрашивания", "before coloring", "przed farbowaniem"),
    "при фарбуванні і освітленні": ("при окрашивании и осветлении", "during coloring and lightening", "podczas farbowania i rozjaśniania"),
    "при фарбуванні": ("при окрашивании", "during coloring", "podczas farbowania"),
    "до стрижки": ("до стрижки", "before haircut", "przed strzyżeniem"),
    "у фарбування": ("в окрашивание", "into coloring", "do farbowania"),
    "в фарбування": ("в окрашивание", "into coloring", "do farbowania"),
    "з лікуванням": ("с лечением", "with treatment", "z leczeniem"),
    "з розвантаженням": ("с разгрузкой", "with offloading", "z odciążeniem"),
    "+ розвантаження": ("+ разгрузка", "+ offloading", "+ odciążenie"),
    "крижане сяйво холодне відновлення": ("ледяное сияние холодное восстановление", "icy glow cold restoration", "lodowy blask zimna regeneracja"),
    "до масажу": ("до массажа", "before massage", "przed masażem"),
    "+сироватка/крем-маска": ("+сыворотка/крем-маска", "+serum/cream mask", "+serum/krem-maska"),
    "один сеанс": ("один сеанс", "one session", "jedna sesja"),
    "*орієнтовна вартість": ("*ориентировочная стоимость", "*estimated cost", "*szacunkowy koszt"),
    "Абсолютне щастя для волосся": ("Абсолютное счастье для волос", "Absolute happiness for hair", "Absolutne szczęście dla włosów"),
    "Щасливе фарбування": ("Счастливое окрашивание", "Happy coloring", "Szczęśliwe farbowanie"),
    "Повне лікування волосся": ("Полное лечение волос", "Full hair treatment", "Pełne leczenie włosów"),
    "Ідеальний догляд": ("Идеальный уход", "Perfect care", "Idealna pielęgnacja"),
    "догляд": ("уход", "care", "pielęgnacja"),
    "Тихий океан": ("Тихий океан", "Pacific Ocean", "Pacyfik"),
    "\"Вогонь та лід\"": ("\"Огонь и лёд\"", "\"Fire and ice\"", "\"Ogień i lód\""),
    "антибандаж": ("антибандаж", "anti-bandage", "anty-bandaż"),
    "антицелюлітний бандаж": ("антицеллюлитный бандаж", "anti-cellulite bandage", "bandaż antycellulitowy"),
    "себорегуляції": ("себорегуляции", "sebum regulation", "seboregulacji"),
    "для відновлення PH балансу": ("для восстановления pH баланса", "for pH balance restoration", "dla przywrócenia równowagi pH"),
    "себорегуляція": ("себорегуляция", "sebum regulation", "seboregulacja"),
    "азелаїновий/мигдалевий пілінг": ("азелаиновый/миндальный пилинг", "azelaic/almond peeling", "peeling azelainowy/migdałowy"),
    "антиоксидантна фреш-терапія": ("антиоксидантная фреш-терапия", "antioxidant fresh therapy", "terapia antyoksydacyjna fresh"),
    "зменшення об'єму підборіддя, рук та живота": ("уменьшение объёма подбородка, рук и живота", "chin, arms and abdomen volume reduction", "redukcja objętości podbródka, rąk i brzucha"),
    "зменшення обʼєму підборіддя,рук та живота": ("уменьшение объёма подбородка, рук и живота", "chin, arms and abdomen volume reduction", "redukcja objętości podbródka, rąk i brzucha"),
    "зволоження обличчя та шиї": ("увлажнение лица и шеи", "face and neck moisturizing", "nawilżanie twarzy i szyi"),
    "лікування темних кіл під очима,розгладження зморшок": ("лечение тёмных кругов под глазами, разглаживание морщин", "dark circles treatment, wrinkle smoothing", "leczenie cieni pod oczami, wygładzanie zmarszczek"),
    "лікування рубців,акне,постакне": ("лечение рубцов, акне, постакне", "scars, acne, post-acne treatment", "leczenie blizn, trądziku, potrądzikowe"),
    "біорепарація шкіри та зволоження": ("биорепарация кожи и увлажнение", "skin bioreparation and moisturizing", "bioreparacja skóry i nawilżanie"),
    "заповнення глибоких зморшок": ("заполнение глубоких морщин", "deep wrinkle filling", "wypełnianie głębokich zmarszczek"),
    "заповнення поверхневих заломів": ("заполнение поверхностных заломов", "superficial fold filling", "wypełnianie powierzchownych załamań"),
    "зона навколо очей": ("зона вокруг глаз", "eye area", "okolice oczu"),
    "обличчя/шия/декольте": ("лицо/шея/декольте", "face/neck/décolleté", "twarz/szyja/dekolt"),
    "обличчя+шия+декольте": ("лицо+шея+декольте", "face+neck+décolleté", "twarz+szyja+dekolt"),
    "обличчя+шия": ("лицо+шея", "face+neck", "twarz+szyja"),
    "стимуляція природних зволожуючих механізмів шкіри від": ("стимуляция естественных увлажняющих механизмов кожи от", "stimulation of natural skin moisturizing mechanisms by", "stymulacja naturalnych mechanizmów nawilżających skóry od"),
    "високотехнологічне рішення для корекціі зморшок від": ("высокотехнологичное решение для коррекции морщин от", "high-tech wrinkle correction solution by", "zaawansowane technologicznie rozwiązanie do korekcji zmarszczek od"),
    "формування V-подібних рис обличчя від": ("формирование V-образных черт лица от", "V-shaped facial features formation by", "formowanie rysów twarzy w kształcie litery V od"),
    "серія експертних процедур подвійного пілінгу від": ("серия экспертных процедур двойного пилинга от", "expert double peeling procedure series by", "seria eksperckich zabiegów podwójnego peelingu od"),
    "біоревіталізація потужний стимулятор енергіі шкіри від": ("биоревитализация мощный стимулятор энергии кожи от", "biorevitalization powerful skin energy stimulator by", "biowitalizacja potężny stymulator energii skóry od"),
    "задоволення потреб чутливої шкіри від": ("удовлетворение потребностей чувствительной кожи от", "satisfying sensitive skin needs by", "zaspokajanie potrzeb skóry wrażliwej od"),
    "регенерація та стимуляція росту волосся": ("регенерация и стимуляция роста волос", "regeneration and hair growth stimulation", "regeneracja i stymulacja wzrostu włosów"),
    "крем для глибокого догляду.": ("крем для глубокого ухода.", "deep care cream.", "krem do głębokiej pielęgnacji."),
    "Айкун": ("Айкун", "Aykun", "Aykun"),
    "обгортання": ("обёртывание", "wrapping", "owijanie"),
    "Локони": ("Локоны", "Curls", "Loki"),
    "віск": ("воск", "wax", "wosk"),
    "Віск": ("Воск", "Wax", "Wosk"),
    "ніготь": ("ноготь", "nail", "paznokieć"),
    "1 ніготь": ("1 ноготь", "1 nail", "1 paznokieć"),
    "зморшки": ("морщины", "wrinkles", "zmarszczki"),
    "\"Зморшки кролика\"": ("\"Морщины кролика\"", "\"Bunny lines\"", "\"Zmarszczki królicze\""),
    "1 одиниця": ("1 единица", "1 unit", "1 jednostka"),
    "1 од": ("1 шт", "1 pc", "1 szt"),
    "1 од.": ("1 шт.", "1 pc.", "1 szt."),
    "(1од)": ("(1шт)", "(1 pc)", "(1 szt)"),
    "1 шт": ("1 шт", "1 pc", "1 szt"),
    "(від 3од)": ("(от 3шт)", "(from 3 pcs)", "(od 3 szt)"),
    "більше10 шт": ("более 10 шт", "more than 10 pcs", "więcej niż 10 szt"),
    "(1 ст)": ("(1 стадия)", "(stage 1)", "(stadium 1)"),
    "(1 стадія)": ("(1 стадия)", "(stage 1)", "(stadium 1)"),
    "(2 стадія з лікуванням)": ("(2 стадия с лечением)", "(stage 2 with treatment)", "(stadium 2 z leczeniem)"),
    "(2 ст з лікуванням)": ("(2 стадия с лечением)", "(stage 2 with treatment)", "(stadium 2 z leczeniem)"),
    "1ступ.1 од.": ("1 степ. 1 шт.", "stage 1, 1 pc.", "1 stopień, 1 szt."),
    "2 ступ.1 од.": ("2 степ. 1 шт.", "stage 2, 1 pc.", "2 stopień, 1 szt."),
    "з гнійним процесом/гранульомою": ("с гнойным процессом/гранулёмой", "with purulent process/granuloma", "z procesem ropnym/ziarniniakiem"),
    "з запальним процесом": ("с воспалительным процессом", "with inflammatory process", "z procesem zapalnym"),
    "поодиноких": ("единичных", "single", "pojedynczych"),
    "мозаїчні, множинні": ("мозаичные, множественные", "mosaic, multiple", "mozaikowe, mnogie"),
    "ноги повністю": ("ноги полностью", "full legs", "nogi w całości"),
    "ноги повністю + пахви": ("ноги полностью + подмышки", "full legs + armpits", "nogi w całości + pachy"),
    "ноги повністю + пахви + глибоке бікіні": ("ноги полностью + подмышки + глубокое бикини", "full legs + armpits + deep bikini", "nogi w całości + pachy + głębokie bikini"),
    "гомілки + коліна": ("голени + колени", "shins + knees", "łydki + kolana"),
    "живіт повністю": ("живот полностью", "full abdomen", "brzuch w całości"),
    "живіт частково": ("живот частично", "partial abdomen", "brzuch częściowo"),
    "пахви": ("подмышки", "armpits", "pachy"),
    "руки повнісю": ("руки полностью", "full arms", "ramiona w całości"),
    "руки частково": ("руки частично", "partial arms", "ramiona częściowo"),
    "сідницi": ("ягодицы", "buttocks", "pośladki"),
    "Процедура для себорегуляції": ("Процедура для себорегуляции", "Sebum regulation procedure", "Zabieg seboregulacji"),
    "Процедура проти випадіння": ("Процедура против выпадения", "Anti-hair loss procedure", "Zabieg przeciw wypadaniu"),
    "Процедура для відновлення PH балансу": ("Процедура для восстановления pH баланса", "pH balance restoration procedure", "Zabieg przywracania równowagi pH"),
    "процедура проти жирної лупи": ("процедура против жирной перхоти", "anti-oily dandruff procedure", "zabieg przeciw tłustemu łupieżowi"),
    "процедура проти сухої лупи": ("процедура против сухой перхоти", "anti-dry dandruff procedure", "zabieg przeciw suchemu łupieżowi"),
    "для співробітиків": ("для сотрудников", "for staff", "dla pracowników"),
    "(чоловіки)": ("(мужчины)", "(men)", "(mężczyźni)"),
    "Вітамінізований догляд": ("Витаминизированный уход", "Vitamin care", "Pielęgnacja witaminowa"),
    "1 мл": ("1 мл", "1 ml", "1 ml"),
    "2 мл": ("2 мл", "2 ml", "2 ml"),
    "5 мл": ("5 мл", "5 ml", "5 ml"),
    "2,5 мл": ("2,5 мл", "2.5 ml", "2,5 ml"),
    "всі зони": ("все зоны", "all zones", "wszystkie strefy"),
    "(вкл.чутливу)": ("(вкл. чувствительную)", "(incl. sensitive)", "(w tym wrażliwą)"),
    "лідокаїн": ("лидокаин", "lidocaine", "lidokaina"),
    "+ лідокаїн": ("+ лидокаин", "+ lidocaine", "+ lidokaina"),
    "Маска для ніг": ("Маска для ног", "Foot mask", "Maska do stóp"),
    "Маска для рук": ("Маска для рук", "Hand mask", "Maska do dłoni"),
    "Скраб для ніг": ("Скраб для ног", "Foot scrub", "Peeling do stóp"),
    "Скраб для рук": ("Скраб для рук", "Hand scrub", "Peeling do dłoni"),
    "Витамін колор": ("Витамин колор", "Vitamin color", "Vitamin color"),
    "Витаминизированный уход": ("Витаминизированный уход", "Vitamin care", "Pielęgnacja witaminowa"),
    "Окрашивание": ("Окрашивание", "Coloring", "Farbowanie"),
    "олія для реконструкції": ("масло для реконструкции", "oil for reconstruction", "olejek do rekonstrukcji"),
    "для чутливоі шкіри голови": ("для чувствительной кожи головы", "for sensitive scalp", "dla wrażliwej skóry głowy"),
    # ── Косметология —
    "Remodeling Face": ("Remodeling Face", "Remodeling Face", "Remodeling Face"),
    "Skin recovery пілінг": ("Skin recovery пилинг", "Skin recovery peeling", "Skin recovery peeling"),
    "класична чистка+пілінг": ("классическая чистка+пилинг", "classic cleansing+peeling", "oczyszczanie klasyczne+peeling"),
    "класична чистка+карбоксітерапія": ("классическая чистка+карбокситерапия", "classic cleansing+carboxytherapy", "oczyszczanie klasyczne+karboksyterapia"),
    "класична чистка+карбоксітерапія апаратна": ("классическая чистка+аппаратная карбокситерапия", "classic cleansing+hardware carboxytherapy", "oczyszczanie klasyczne+karboksyterapia aparaturowa"),
    "класична чистка+мікротокова терапія": ("классическая чистка+микротоковая терапия", "classic cleansing+microcurrent therapy", "oczyszczanie klasyczne+terapia mikropradowa"),
    "класична чистка+маска Alginat": ("классическая чистка+маска Alginat", "classic cleansing+Alginat mask", "oczyszczanie klasyczne+maska Alginat"),
    "класична чистка+гальванізація": ("классическая чистка+гальванизация", "classic cleansing+galvanization", "oczyszczanie klasyczne+galwanizacja"),
    "класична чистка+безголкова мезотерапія": ("классическая чистка+безигольная мезотерапия", "classic cleansing+needle-free mesotherapy", "oczyszczanie klasyczne+mezoterapia bezigłowa"),
    "вакуумно-водневий пілінг": ("вакуумно-водородный пилинг", "vacuum-hydrogen peeling", "peeling próżniowo-wodorowy"),
    "вакуумно-водородный пілинг": ("вакуумно-водородный пилинг", "vacuum-hydrogen peeling", "peeling próżniowo-wodorowy"),
    "альгинатная маска": ("альгинатная маска", "alginate mask", "maska alginatowa"),
    "Спа кератин": ("Спа кератин", "Spa keratin", "Spa keratyna"),
    "SPA-кератин": ("SPA-кератин", "SPA-keratin", "SPA-keratyna"),
}

# ── Переклад ────────────────────────────────────────────────────────

def translate_name(name: str) -> dict[str, str]:
    """Перекладає назву послуги UK → RU/EN/PL.

    Повертає {"uk": ..., "ru": ..., "en": ..., "pl": ...}
    """
    uk = name
    ru = name
    en = name
    pl = name

    # 1. Витягуємо рівень (МАЙСТЕР/ТОП/АРТ/БАРБЕР) з кінця
    level_suffix = {"ru": "", "en": "", "pl": ""}
    stripped = name.rstrip()
    for lvl, translations in LEVELS.items():
        if stripped.endswith(" " + lvl) or stripped == lvl:
            level_suffix = {k: " " + v for k, v in translations.items()}
            stripped = stripped[:-(len(lvl))].rstrip()
            break

    core = stripped

    # 2. Витягуємо довжину
    length_suffix = {"ru": "", "en": "", "pl": ""}

    # "N/M довжина" pattern
    m = LENGTH_RANGE_RE.search(core)
    if m:
        nums = f"{m.group(1)}/{m.group(2)}"
        length_suffix = {"ru": f" {nums} длина", "en": f" length {nums}", "pl": f" długość {nums}"}
        core = core[:m.start()].rstrip() + core[m.end():].lstrip()
        core = core.strip()
    else:
        m = LENGTH_RE.search(core)
        if m:
            n = m.group(1)
            length_suffix = {"ru": f" {n} длина", "en": f" length {n}", "pl": f" długość {n}"}
            core = core[:m.start()].rstrip() + core[m.end():].lstrip()
            core = core.strip()
        else:
            m = LENGTH_ALT_RE.search(core)
            if m:
                n = m.group(1)
                length_suffix = {"ru": f" длина {n}", "en": f" length {n}", "pl": f" długość {n}"}
                core = core[:m.start()].rstrip() + core[m.end():].lstrip()
                core = core.strip()

    # 3. Перекладаємо core через фразовий словник (longest match first)
    core_ru, core_en, core_pl = _translate_core(core)

    ru = core_ru + length_suffix["ru"] + level_suffix.get("ru", "")
    en = core_en + length_suffix["en"] + level_suffix.get("en", "")
    pl = core_pl + length_suffix["pl"] + level_suffix.get("pl", "")

    return {"uk": uk, "ru": ru.strip(), "en": en.strip(), "pl": pl.strip()}


def _translate_core(text: str) -> tuple[str, str, str]:
    """Перекладає core текст через фразовий словник."""
    if not text:
        return ("", "", "")

    # Спробуємо точний match
    key = text.lower().strip()
    for phrase, (ru, en, pl) in PHRASES.items():
        if key == phrase.lower():
            return (ru, en, pl)

    # Longest-match заміна по частинах
    ru_result = text
    en_result = text
    pl_result = text

    # Сортуємо фрази за довжиною (найдовші спочатку)
    sorted_phrases = sorted(PHRASES.keys(), key=len, reverse=True)

    for phrase in sorted_phrases:
        ru_val, en_val, pl_val = PHRASES[phrase]
        # Case-insensitive пошук
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        if pattern.search(ru_result):
            ru_result = pattern.sub(ru_val, ru_result)
            en_result = pattern.sub(en_val, en_result)
            pl_result = pattern.sub(pl_val, pl_result)

    return (ru_result, en_result, pl_result)


# ── DB operations ──────────────────────────────────────────────────

from app.infrastructure.db.models.catalog import Service

BATCH = 500


async def process_country(country: str, factory, force: bool, dry_run: bool) -> int:
    async with country_session(factory, country) as session:
        stmt = select(Service).where(Service.archive.is_(False))
        if not force:
            stmt = stmt.where(Service.name_uk.is_(None))
        services = list((await session.execute(stmt)).scalars().all())

    total = len(services)
    print(f"[{country}] {total} services to translate", flush=True)
    if not total:
        return 0

    done = 0
    t0 = time.time()

    for i in range(0, total, BATCH):
        chunk = services[i : i + BATCH]

        translations = []
        for svc in chunk:
            # Спочатку спробуємо парсити CRM-формат (GB)
            parsed = parse_multilingual_name(svc.name)
            if parsed:
                # parsed має uk + en, потрібні ru + pl
                src = parsed.get("uk") or parsed.get("en") or svc.name
                trans = translate_name(src)
                # Зберігаємо оригінальні UK та EN з CRM
                if "uk" in parsed:
                    trans["uk"] = parsed["uk"]
                if "en" in parsed:
                    trans["en"] = parsed["en"]
                translations.append(trans)
            else:
                # Визначаємо мову
                src_lang = detect_source_lang(svc.name)
                if src_lang == "uk":
                    translations.append(translate_name(svc.name))
                else:
                    # Для не-UK мов — залишаємо name як є в оригінальній мові,
                    # решту заповнюємо як є (без словника для PL/RU/EN→інші)
                    trans = {"uk": svc.name, "ru": svc.name, "en": svc.name, "pl": svc.name}
                    translations.append(trans)

        if not dry_run:
            async with country_session(factory, country) as session:
                for svc, trans in zip(chunk, translations):
                    await session.execute(
                        update(Service).where(Service.id == svc.id).values(
                            name_uk=trans["uk"],
                            name_ru=trans["ru"],
                            name_en=trans["en"],
                            name_pl=trans["pl"],
                        )
                    )

        done += len(chunk)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        print(f"[{country}] {done}/{total} — {rate:.0f} svc/s", flush=True)

    return done


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=["ua", "pl", "gb"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    countries = [args.country] if args.country else ["ua", "pl", "gb"]
    engine = build_engine()
    factory = build_session_factory(engine)
    total = 0
    t0 = time.time()
    try:
        for c in countries:
            total += await process_country(c, factory, args.force, args.dry_run)
    finally:
        await engine.dispose()
    print(f"DONE. {total} services in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(amain())
