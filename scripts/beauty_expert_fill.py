"""Beauty Expert Engine — масове заповнення профілів з експертними знаннями.

Категоризує кожну послугу за назвою і заповнює structured поля
на основі глибоких знань beauty-індустрії.

Запуск:
    python -m scripts.beauty_expert_fill --dry-run      # подивитись скільки
    python -m scripts.beauty_expert_fill --apply         # записати в БД
"""
from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass, field

from sqlalchemy import text as sql_text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory
from app.domain.services.canonical_key import _ADDON_RE, _LENGTH_PATTERNS, _LEVEL_RE


def clean_display_name(name: str) -> str:
    clean = _ADDON_RE.sub(" ", name)
    for pat in _LENGTH_PATTERNS:
        clean = pat.sub(" ", clean)
    while True:
        new = _LEVEL_RE.sub("", clean).strip()
        if new == clean:
            break
        clean = new
    return re.sub(r"\s+", " ", clean).strip()


# ══════════════════════════════════════════════════════════════════
# BEAUTY EXPERT KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════

@dataclass
class ProfileData:
    short_description: str = ""
    addresses_problems: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    sales_pitch: str = ""
    cross_sell: list[str] = field(default_factory=list)
    target_audience: list[str] = field(default_factory=list)
    procedure_steps: list[str] = field(default_factory=list)
    contraindications: list[str] = field(default_factory=list)
    aftercare_advice: str | None = None


def categorize_and_fill(name: str, canonical_key: str) -> ProfileData:
    """Категоризує послугу і заповнює профіль як beauty-експерт."""
    n = name.lower()
    k = canonical_key.lower()

    # ── ВОЛОССЯ: Фарбування ───────────────────────────────────────
    if _is_hair_color(n, k):
        return _fill_hair_color(name, n, k)

    # ── ВОЛОССЯ: Стрижка ──────────────────────────────────────────
    if _is_hair_cut(n, k):
        return _fill_hair_cut(name, n, k)

    # ── ВОЛОССЯ: Укладка / Зачіска ────────────────────────────────
    if _is_hair_style(n, k):
        return _fill_hair_style(name, n, k)

    # ── ВОЛОССЯ: Догляд / Відновлення ─────────────────────────────
    if _is_hair_care(n, k):
        return _fill_hair_care(name, n, k)

    # ── МАНІКЮР ───────────────────────────────────────────────────
    if _is_manicure(n, k):
        return _fill_manicure(name, n, k)

    # ── ПЕДИКЮР ───────────────────────────────────────────────────
    if _is_pedicure(n, k):
        return _fill_pedicure(name, n, k)

    # ── НІГТІ (нарощування, корекція, зняття) ─────────────────────
    if _is_nails(n, k):
        return _fill_nails(name, n, k)

    # ── БРОВИ ─────────────────────────────────────────────────────
    if _is_brows(n, k):
        return _fill_brows(name, n, k)

    # ── ВІЇ ───────────────────────────────────────────────────────
    if _is_lashes(n, k):
        return _fill_lashes(name, n, k)

    # ── МАКІЯЖ ────────────────────────────────────────────────────
    if _is_makeup(n, k):
        return _fill_makeup(name, n, k)

    # ── ОБЛИЧЧЯ (чистка, пілінг, догляд, ін'єкції) ────────────────
    if _is_face(n, k):
        return _fill_face(name, n, k)

    # ── МАСАЖ ─────────────────────────────────────────────────────
    if _is_massage(n, k):
        return _fill_massage(name, n, k)

    # ── ДЕПІЛЯЦІЯ / ЕПІЛЯЦІЯ ──────────────────────────────────────
    if _is_depilation(n, k):
        return _fill_depilation(name, n, k)

    # ── ТІЛО (обгортання, антицелюліт) ────────────────────────────
    if _is_body(n, k):
        return _fill_body(name, n, k)

    # ── ПОДОЛОГІЯ ─────────────────────────────────────────────────
    if _is_podology(n, k):
        return _fill_podology(name, n, k)

    # ── COSMETOLOGY / ІН'ЄКЦІЇ ────────────────────────────────────
    if _is_cosmetology(n, k):
        return _fill_cosmetology(name, n, k)

    # ── FALLBACK ──────────────────────────────────────────────────
    return _fill_generic(name, n, k)


# ══════════════════════════════════════════════════════════════════
# CATEGORY DETECTION
# ══════════════════════════════════════════════════════════════════

def _is_hair_color(n, k):
    return any(w in n for w in [
        "фарбуван", "тонуван", "мелірув", "мелирован", "освітлен", "блонд",
        "колір", "color", "балаяж", "balayage", "airtouch", "shatush",
        "шатуш", "highlights", "highlight", "baby light", "babylight",
        "контуринг", "handtouch", "вуаль", "зняття кольор", "органічн",
        "передпігмент", "color&gloss", "блонд мит", "вихід з чорн",
        "вихід із чорн",
    ])

def _is_hair_cut(n, k):
    return any(w in n for w in [
        "стрижк", "підрівн", "haircut", "strzy", "стрижен",
    ]) and "чубч" not in n or "стрижка" in n

def _is_hair_style(n, k):
    return any(w in n for w in [
        "уклад", "зачіск", "локон", "плетін", "афро", "браш", "голівуд",
        "хвиля", "накрут",
    ])

def _is_hair_care(n, k):
    return any(w in n for w in [
        "відновлен", "реконструкц", "ботокс для волос", "кератин",
        "ламінуван волос", "spa ", "спа ", "маск", "ампул",
        "lebel", "olaplex", "olapex", "brae", "sorbie", "hadat", "k18",
        "orising", "milbon", "napla", "nashi", "tokio", "innovat",
        "keune", "kevin murphy", "link-d", "luminoil", "awapuhi",
        "bond angel", "щастя для волос", "lanza", "cronna",
        "догляд за волосс", "лікування волосс", "захист при фарб",
        "захист волосся", "пілінг шкіри голов", "скраб", "полірув",
        "детокс", "біозавив", "випрямлен",
    ])

def _is_manicure(n, k):
    return any(w in n for w in [
        "манікюр", "manicur", "manic", "маник",
    ]) and "педикюр" not in n

def _is_pedicure(n, k):
    return any(w in n for w in [
        "педикюр", "pedicur", "педік", "pedyk", "стоп",
    ])

def _is_nails(n, k):
    return any(w in n for w in [
        "нарощ", "корекц нарощ", "нігт", "гель-лак", "шеллак",
        "gel-lak", "лак ", "френч", "укріплен", "зняття гель",
        "типс", "покрит", "ibx", "дизайн",
    ]) and "бров" not in n and "вій" not in n

def _is_brows(n, k):
    return any(w in n for w in [
        "брів", "бров", "brow", "біофікс",
    ])

def _is_lashes(n, k):
    return any(w in n for w in [
        "вій", "вії", "ресниц", "lash", "пучок",
    ]) and "бров" not in n

def _is_makeup(n, k):
    return any(w in n for w in [
        "макіяж", "makeup", "візаж", "визаж",
    ])

def _is_face(n, k):
    return any(w in n for w in [
        "обличч", "чистк", "пілінг", "маска для облич", "face",
        "ліфтинг", "rf-", "smas", "мезотерап", "біоревіт",
        "ботокс", "botox", "філер", "контурн", "гіалурон",
        "ін'єкц", "карбокс", "мікростр", "електропорац",
        "фотоомолод", "альгінат", "кріо", "оксигенац", "пілінг",
        "hydra", "casmara", "dmk", "biologique", "hydropeptide",
        "зморшк", "rejuran", "belotero", "juvederm", "stylage",
        "radiesse", "teosyal", "alluzience", "botulinoter",
        "aesthefill", "neauvia", "karisma", "plenhyage",
    ])

def _is_massage(n, k):
    return any(w in n for w in [
        "масаж", "massage", "massazh", "масса",
    ])

def _is_depilation(n, k):
    return any(w in n for w in [
        "депіляц", "епіляц", "лазер", "бікіні", "bikini",
        "воскова", "шугаринг", "soprano",
    ])

def _is_body(n, k):
    return any(w in n for w in [
        "тіло", "body", "обгорт", "целюл", "arosha", "endosph",
        "kuma shape", "icoone", "айкун", "стратосфер", "бандаж",
    ])

def _is_podology(n, k):
    return any(w in n for w in [
        "подолог", "врослий", "бородавк", "мозол", "оніхолі",
        "оніхомік", "тріщин", "протезуван", "тейпуван", "bs пластин",
        "скоб", "корекційн систем", "титанов нит",
    ])

def _is_cosmetology(n, k):
    return any(w in n for w in [
        "консультац", "діагност", "robolex", "endosphere",
        "ліпо", "нитк", "aptos", "powerfill", "дренаж",
    ])


# ══════════════════════════════════════════════════════════════════
# FILL FUNCTIONS — BEAUTY EXPERT CONTENT
# ══════════════════════════════════════════════════════════════════

def _fill_hair_color(name, n, k) -> ProfileData:
    d = ProfileData()
    clean = clean_display_name(name)

    if "тонуван" in n:
        d.short_description = f"{clean} — безаміачне тонування для оновлення та вирівнювання кольору. Не пошкоджує структуру волосся, додає блиск і насиченість."
        d.addresses_problems = ["тьмяний колір", "жовтизна після освітлення", "нерівномірний колір", "втрачена насиченість"]
        d.benefits = ["оновлений колір", "блиск", "рівномірний тон", "не пошкоджує волосся"]
        d.keywords = ["тонування", "освіжити колір", "жовтизна", "блонд", "блиск", "тьмяне волосся", "оновити"]
        d.sales_pitch = "Тонування освіжить колір і додасть блиску без шкоди для волосся. Раджу робити кожні 3-4 тижні."
        d.cross_sell = ["догляд для фарбованого волосся", "стрижка кінчиків", "укладка"]
    elif "мелірув" in n or "мелиров" in n or "highlight" in n:
        tech = "мелірування"
        if "балаяж" in n or "balayage" in n:
            tech = "балаяж"
        elif "шатуш" in n or "shatush" in n:
            tech = "шатуш"
        elif "airtouch" in n:
            tech = "Airtouch"
        elif "baby" in n:
            tech = "Baby lights"
        elif "вуаль" in n:
            tech = "мелірування Вуаль"
        elif "контуринг" in n:
            tech = "контуринг"

        d.short_description = f"{clean} — техніка часткового освітлення волосся ({tech}) для створення природних переливів та об'єму кольору."
        d.addresses_problems = ["плоский колір", "хочу додати глибини", "хочу освітлити без повного блонду", "хочу природний ефект"]
        d.benefits = ["природні переливи", "візуальний об'єм", "плавний перехід", "довге відростання без контрастів"]
        d.keywords = [tech, "освітлення", "пасма", "блонд", "переливи", "об'єм кольору", "природний ефект", "фарбування"]
        d.sales_pitch = f"Техніка {tech} створює природні переливи і об'єм. Корекція раз на 3-5 місяців, тонування кожні 3-4 тижні."
        d.cross_sell = ["тонування", "захист волосся", "догляд після фарбування"]
    elif "зняття кольор" in n or "вихід з чорн" in n or "вихід із чорн" in n:
        d.short_description = f"{clean} — видалення небажаного пігменту з волосся. Делікатна процедура, що потребує кваліфікації колориста."
        d.addresses_problems = ["хочу змінити колір", "темне волосся", "невдале фарбування", "хочу вийти з чорного"]
        d.benefits = ["можливість зміни кольору", "підготовка до нового фарбування"]
        d.keywords = ["зняття кольору", "вихід з чорного", "змивка", "освітлення", "змінити колір"]
        d.sales_pitch = "Зняття кольору потребує консультації з майстром. Раджу прийти на огляд, щоб оцінити стан волосся і спланувати процес."
        d.cross_sell = ["захист волосся", "тонування", "догляд після освітлення"]
        d.contraindications = ["дуже пошкоджене волосся без попередньої консультації"]
    elif "фарбуван" in n or "окрашив" in n:
        area = ""
        if "корен" in n or "коріння" in n:
            area = "коренів"
            d.short_description = f"{clean} — оновлення кольору відрослої прикореневої зони. Підтримує рівномірний колір по всій довжині."
            d.addresses_problems = ["відросли корені", "видно різницю з фарбованим волоссям", "сивина на коренях"]
        elif "total blonde" in n or "тотал блонд" in n:
            d.short_description = f"{clean} — однотонне освітлення волосся до чистого блонду по всій довжині."
            d.addresses_problems = ["хочу чистий блонд", "хочу однотонний світлий колір"]
        else:
            d.short_description = f"{clean} — професійне фарбування волосся якісним барвником з підбором оптимального відтінку."
            d.addresses_problems = ["сивина", "хочу змінити колір", "тьмяний колір", "хочу яскравий відтінок"]

        d.benefits = ["насичений стійкий колір", "блиск", "покриття сивини", "професійний підбір відтінку"]
        d.keywords = ["фарбування", "колір", "пофарбувати", "сивина", "корені", "відтінок", "блиск"]
        d.sales_pitch = "Використовуємо професійний барвник з захистом структури. Майстер підбере ідеальний відтінок під вашу шкіру."
        d.cross_sell = ["тонування через 3-4 тижні", "догляд для фарбованого волосся", "укладка"]
    else:
        d.short_description = f"{clean} — професійна процедура зміни або корекції кольору волосся."
        d.addresses_problems = ["хочу змінити колір", "невдоволення поточним кольором"]
        d.benefits = ["оновлений вигляд", "професійний результат"]
        d.keywords = ["колір", "фарбування", "волосся", clean.lower()]
        d.sales_pitch = f"Раджу {clean.lower()} для оновлення вашого образу. Майстер проконсультує щодо оптимального варіанту."
        d.cross_sell = ["догляд для волосся", "укладка"]

    d.target_audience = ["жінки", "хто фарбує волосся"]
    return d


def _fill_hair_cut(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    is_male = "чоловіч" in n or "мужск" in n or "men" in n or "барбер" in n
    is_child = "дитяч" in n or "детск" in n or "child" in n or "дітей" in n

    d = ProfileData()
    if is_child:
        d.short_description = f"{clean} — акуратна стрижка для дитини в комфортній атмосфері."
        d.addresses_problems = ["дитині потрібна стрижка", "відросло волосся"]
        d.target_audience = ["діти", "батьки"]
        d.keywords = ["дитяча стрижка", "стрижка для дитини", "дитина", "підстригти дитину"]
        d.sales_pitch = "Наші майстри мають досвід роботи з дітьми. Створимо комфортну атмосферу для вашого малюка."
        d.cross_sell = ["стрижка для мами/тата паралельно"]
    elif is_male:
        d.short_description = f"{clean} — чоловіча стрижка з урахуванням форми обличчя та стилю. Включає миття та укладку."
        d.addresses_problems = ["потрібна чоловіча стрижка", "відросло волосся", "хочу новий стиль"]
        d.target_audience = ["чоловіки"]
        d.keywords = ["чоловіча стрижка", "стрижка", "барбер", "підстригтися", "чоловічий зал"]
        d.sales_pitch = "Чоловіча стрижка з урахуванням форми обличчя і стилю. Включає миття і укладку."
        d.cross_sell = ["борода та вуса", "камуфляж сивини"]
    else:
        d.short_description = f"{clean} — індивідуально підібрана форма з діагностикою волосся, миттям та укладкою."
        d.addresses_problems = ["посічені кінчики", "втрата форми", "відросле волосся", "хочу нову стрижку"]
        d.target_audience = ["жінки"]
        d.keywords = ["стрижка", "підстригти", "освіжити стрижку", "кінчики", "форма", "жіноча стрижка"]
        d.sales_pitch = "Стрижка з миттям і укладкою. Раджу освіжувати кінчики раз на 2-3 місяці для здорового вигляду."
        d.cross_sell = ["оформлення брів", "догляд за волоссям", "укладка"]

    d.benefits = ["охайна форма", "акуратний вигляд", "укладка в комплексі", "професійний підбір"]
    return d


def _fill_hair_style(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — професійна укладка або зачіска з використанням якісних стайлінгових засобів."
    d.addresses_problems = ["потрібна укладка", "захід", "фотосесія", "хочу гарно виглядати", "особлива подія"]
    d.benefits = ["професійний результат", "тримається весь день", "об'єм і блиск"]
    d.keywords = ["укладка", "зачіска", "локони", "браш", "об'єм", "захід", "фотосесія", "весілля"]
    d.sales_pitch = "Професійна укладка для будь-якого приводу. Можу одразу додати макіяж паралельно."
    d.cross_sell = ["макіяж", "стрижка", "оформлення брів"]
    d.target_audience = ["жінки", "для особливих подій"]
    return d


def _fill_hair_care(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()

    # Визначаємо бренд/тип
    brand = ""
    if "lebel" in n: brand = "Lebel"
    elif "olaplex" in n or "olapex" in n: brand = "Olaplex"
    elif "brae" in n: brand = "Brae"
    elif "sorbie" in n: brand = "Dr.Sorbie"
    elif "hadat" in n: brand = "Hadat"
    elif "k18" in n: brand = "K18"
    elif "orising" in n: brand = "Orising"
    elif "milbon" in n or "cronna" in n: brand = "Milbon CRONNA"
    elif "tokio" in n or "inkarami" in n: brand = "Tokio Inkarami"
    elif "keune" in n: brand = "Keune"
    elif "napla" in n: brand = "Napla"
    elif "kevin murphy" in n: brand = "Kevin Murphy"

    brand_text = f" ({brand})" if brand else ""

    if "захист" in n or "protection" in n:
        d.short_description = f"{clean} — захист волосся під час фарбування або освітлення{brand_text}. Мінімізує пошкодження структури."
        d.addresses_problems = ["пошкодження при фарбуванні", "ламкість після освітлення"]
        d.benefits = ["захист структури", "зменшення ламкості", "збереження якості волосся"]
        d.keywords = ["захист волосся", "при фарбуванні", "bond", "olaplex", "захист"]
    elif "ампул" in n:
        d.short_description = f"{clean} — концентрований догляд{brand_text} для миттєвого відновлення і блиску волосся."
        d.addresses_problems = ["тьмяне волосся", "сухість", "після фарбування"]
        d.benefits = ["миттєвий ефект", "блиск", "живлення"]
        d.keywords = ["ампула", "миттєвий догляд", "відновлення", "блиск"]
    elif "маск" in n:
        d.short_description = f"{clean} — інтенсивний догляд{brand_text} з глибоким живленням і відновленням структури волосся."
        d.addresses_problems = ["сухість", "ламкість", "пошкоджене волосся"]
        d.benefits = ["глибоке живлення", "м'якість", "блиск", "відновлення"]
        d.keywords = ["маска для волосся", "живлення", "відновлення", "глибокий догляд"]
    elif "пілінг" in n or "скраб" in n or "детокс" in n:
        d.short_description = f"{clean} — очищення шкіри голови{brand_text} від себуму, залишків стайлінгу та мертвих клітин."
        d.addresses_problems = ["жирна шкіра голови", "лупа", "забиті пори", "повільний ріст волосся"]
        d.benefits = ["чиста шкіра голови", "покращений кровообіг", "стимуляція росту"]
        d.keywords = ["пілінг голови", "скраб", "детокс", "очищення", "жирність", "лупа"]
    elif "кератин" in n or "випрямлен" in n:
        d.short_description = f"{clean} — процедура кератинового відновлення{brand_text} для гладкості, блиску та зменшення пухнастості."
        d.addresses_problems = ["пухнасте волосся", "кучерявість", "важко розчісувати", "відсутність блиску"]
        d.benefits = ["гладкість", "блиск", "легке розчісування", "зменшення пухнастості"]
        d.keywords = ["кератин", "випрямлення", "гладке волосся", "блиск", "пухнастість"]
        d.contraindications = ["вагітність", "годування груддю"]
    elif "біозавив" in n:
        d.short_description = f"{clean} — м'яка завивка волосся{brand_text} зі збереженням структури. Тримається 3-6 місяців."
        d.addresses_problems = ["пряме волосся", "хочу кучері", "хочу об'єм"]
        d.benefits = ["тривалий об'єм", "природні кучері", "м'яка формула"]
        d.keywords = ["завивка", "кучері", "об'єм", "біозавивка"]
    else:
        d.short_description = f"{clean} — професійний догляд{brand_text} для відновлення, живлення і блиску волосся."
        d.addresses_problems = ["сухість", "ламкість", "тьмяність", "пошкоджене волосся", "після фарбування"]
        d.benefits = ["відновлення структури", "блиск", "м'якість", "захист кольору"]
        d.keywords = ["догляд", "відновлення", "блиск", "лікування волосся", brand.lower() if brand else "волосся"]

    d.sales_pitch = f"Раджу {clean.lower()} для покращення стану волосся. Ефект помітний одразу після процедури."
    d.cross_sell = ["стрижка кінчиків", "укладка", "тонування"]
    d.target_audience = ["жінки з пошкодженим або фарбованим волоссям"]
    return d


def _fill_manicure(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    is_male = "чоловіч" in n or "мужск" in n
    is_spa = "spa" in n or "спа" in n
    is_japanese = "японськ" in n

    d = ProfileData()
    if is_spa:
        d.short_description = f"{clean} — SPA-манікюр з розширеним доглядом: скраб, маска, масаж рук."
        d.addresses_problems = ["суха шкіра рук", "втомлені руки", "хочу релакс"]
        d.keywords = ["spa манікюр", "догляд для рук", "скраб", "маска", "масаж рук"]
    elif is_japanese:
        d.short_description = f"{clean} — японський манікюр з природним блиском нігтів без покриття лаком."
        d.addresses_problems = ["ламкі нігті", "хочу природний вигляд", "не хочу лак"]
        d.keywords = ["японський манікюр", "природний блиск", "зміцнення нігтів", "без лаку"]
    elif is_male:
        d.short_description = f"{clean} — чоловічий манікюр: обробка кутикули, форма, шліфування."
        d.addresses_problems = ["хочу доглянуті руки", "задирки", "нерівні нігті"]
        d.keywords = ["чоловічий манікюр", "чоловічий догляд", "нігті"]
        d.target_audience = ["чоловіки"]
    else:
        d.short_description = f"{clean} — професійний манікюр з обробкою кутикули та формуванням нігтів."
        d.addresses_problems = ["потрібен манікюр", "задирки", "нерівна кутикула"]
        d.keywords = ["манікюр", "нігті", "кутикула", "доглянуті руки"]

    d.benefits = ["охайні нігті", "доглянуті руки", "акуратна кутикула"]
    d.sales_pitch = f"Раджу {clean.lower()} для охайного та доглянутого вигляду рук."
    d.cross_sell = ["гель-лак", "spa для рук", "оформлення брів"]
    if not d.target_audience:
        d.target_audience = ["всі"]
    return d


def _fill_pedicure(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    is_male = "чоловіч" in n or "мужск" in n
    is_medical = "медичн" in n or "подолог" in n

    d = ProfileData()
    if is_medical:
        d.short_description = f"{clean} — медичний педикюр від подолога з професійною обробкою проблемних зон."
        d.addresses_problems = ["проблемні нігті", "грибок", "врослий ніготь", "тріщини стоп"]
        d.keywords = ["медичний педикюр", "подолог", "лікувальний", "проблемні нігті"]
    elif is_male:
        d.short_description = f"{clean} — чоловічий педикюр з обробкою стоп та нігтів."
        d.addresses_problems = ["груба шкіра стоп", "натоптиші"]
        d.keywords = ["чоловічий педикюр", "стопи", "чоловічий догляд"]
        d.target_audience = ["чоловіки"]
    else:
        d.short_description = f"{clean} — класичний педикюр з обробкою стоп, кутикули та формуванням нігтів."
        d.addresses_problems = ["груба шкіра стоп", "тріщини", "натоптиші", "хочу доглянуті ноги"]
        d.keywords = ["педикюр", "стопи", "ноги", "натоптиші", "доглянуті ноги"]

    d.benefits = ["м'яка шкіра стоп", "охайні нігті", "комфорт"]
    d.sales_pitch = f"Раджу {clean.lower()} для здорових та доглянутих ніг. Робити раз на 4-6 тижнів."
    d.cross_sell = ["манікюр", "spa для ніг", "покриття гель-лак"]
    if not d.target_audience:
        d.target_audience = ["всі"]
    return d


def _fill_nails(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    if "нарощ" in n:
        d.short_description = f"{clean} — нарощування або корекція нігтів для бажаної довжини і форми."
        d.addresses_problems = ["короткі нігті", "ламкі нігті", "хочу довгі нігті"]
        d.keywords = ["нарощування", "довгі нігті", "гель", "акрил", "корекція"]
    elif "гель-лак" in n or "gel" in n or "покрит" in n:
        d.short_description = f"{clean} — стійке покриття гель-лаком з великим вибором кольорів. Тримається 2-3 тижні."
        d.addresses_problems = ["хочу стійке покриття", "лак швидко облазить", "шеллак"]
        d.keywords = ["гель-лак", "покриття", "шеллак", "стійкий лак", "блиск"]
    elif "зняття" in n:
        d.short_description = f"{clean} — безпечне зняття покриття з мінімальним впливом на нігтьову пластину."
        d.addresses_problems = ["треба зняти покриття", "старий гель-лак"]
        d.keywords = ["зняття", "зняти гель-лак", "зняти покриття"]
    elif "укріплен" in n:
        d.short_description = f"{clean} — зміцнення нігтьової пластини для запобігання ламкості і розшаруванню."
        d.addresses_problems = ["ламкі нігті", "тонкі нігті", "розшаровуються"]
        d.keywords = ["укріплення", "зміцнення", "ламкі нігті"]
    else:
        d.short_description = f"{clean} — професійна процедура для нігтів."
        d.keywords = [clean.lower(), "нігті"]

    d.benefits = ["охайний вигляд", "стійкий результат"]
    d.sales_pitch = f"Раджу {clean.lower()} для ідеального результату."
    d.cross_sell = ["манікюр", "педикюр", "spa для рук"]
    d.target_audience = ["всі"]
    return d


def _fill_brows(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    if "ламінуван" in n:
        d.short_description = f"{clean} — ламінування брів для фіксації форми та об'єму на 4-6 тижнів."
        d.addresses_problems = ["неслухняні брови", "тонкі брови", "хочу густіші брови"]
    elif "фарбуван" in n:
        d.short_description = f"{clean} — фарбування брів для насиченого кольору та виразного вигляду."
        d.addresses_problems = ["світлі брови", "хочу виразні брови", "нерівномірний колір"]
    elif "корекц" in n:
        d.short_description = f"{clean} — корекція форми брів для підкреслення рис обличчя."
        d.addresses_problems = ["неохайні брови", "несиметричні", "хочу гарну форму"]
    else:
        d.short_description = f"{clean} — професійне оформлення брів для виразного погляду."
        d.addresses_problems = ["хочу гарні брови", "неохайна форма"]

    d.benefits = ["виразний погляд", "підкреслені риси обличчя", "охайна форма"]
    d.keywords = ["брови", "оформлення брів", "корекція", "фарбування", "ламінування"]
    d.sales_pitch = f"Раджу {clean.lower()} для підкреслення природної краси вашого обличчя."
    d.cross_sell = ["фарбування вій", "ламінування вій", "макіяж"]
    d.target_audience = ["всі"]
    return d


def _fill_lashes(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    if "нарощ" in n:
        d.short_description = f"{clean} — нарощування вій для об'єму, довжини та виразного погляду."
        d.addresses_problems = ["короткі вії", "рідкі вії", "хочу об'ємні вії"]
        d.keywords = ["нарощування вій", "об'ємні вії", "довгі вії", "2D", "3D"]
    elif "ламінуван" in n:
        d.short_description = f"{clean} — ламінування вій з фіксацією вигину. Тримається 4-6 тижнів."
        d.addresses_problems = ["прямі вії", "хочу вигин", "не хочу нарощування"]
        d.keywords = ["ламінування вій", "вигин", "природні вії", "без нарощування"]
    elif "фарбуван" in n:
        d.short_description = f"{clean} — фарбування вій для глибокого кольору без щоденного макіяжу."
        d.addresses_problems = ["світлі вії", "хочу без туші"]
        d.keywords = ["фарбування вій", "темні вії", "без туші"]
    else:
        d.short_description = f"{clean} — професійна процедура для вій."
        d.keywords = ["вії", clean.lower()]

    d.benefits = ["виразний погляд", "без щоденного макіяжу"]
    d.sales_pitch = f"Раджу {clean.lower()} для виразного погляду кожного дня."
    d.cross_sell = ["оформлення брів", "ламінування брів"]
    d.target_audience = ["жінки"]
    return d


def _fill_makeup(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — професійний макіяж з підбором відтінків та техніки під ваш тип шкіри та захід."
    d.addresses_problems = ["потрібен макіяж", "особлива подія", "фотосесія", "весілля"]
    d.benefits = ["професійний результат", "стійкість весь день", "підкреслення природної краси"]
    d.keywords = ["макіяж", "візажист", "мейкап", "весільний", "вечірній", "денний"]
    d.sales_pitch = "Наш візажист підбере ідеальний макіяж під ваш стиль та подію. Можу запропонувати укладку паралельно."
    d.cross_sell = ["укладка", "зачіска", "оформлення брів"]
    d.target_audience = ["жінки", "для особливих подій"]
    return d


def _fill_face(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()

    if "чистк" in n:
        d.short_description = f"{clean} — професійне очищення шкіри обличчя від забруднень, комедонів та висипань."
        d.addresses_problems = ["чорні цятки", "забиті пори", "жирна шкіра", "висипання", "тьмяна шкіра"]
        d.benefits = ["чиста шкіра", "звужені пори", "здоровий колір обличчя"]
        d.keywords = ["чистка обличчя", "очищення", "чорні цятки", "пори", "акне"]
    elif "пілінг" in n:
        d.short_description = f"{clean} — пілінг для оновлення шкіри, вирівнювання тону та текстури."
        d.addresses_problems = ["тьмяна шкіра", "нерівний тон", "пігментація", "дрібні зморшки"]
        d.benefits = ["оновлена шкіра", "рівний тон", "сяйво"]
        d.keywords = ["пілінг", "оновлення шкіри", "тон", "текстура", "сяйво"]
    elif "ботокс" in n or "botox" in n or "ботулін" in n or "alluzience" in n:
        d.short_description = f"{clean} — ін'єкції для розгладження мімічних зморшок та корекції рис обличчя."
        d.addresses_problems = ["мімічні зморшки", "зморшки на лобі", "гусячі лапки", "міжбрів'я"]
        d.benefits = ["розгладження зморшок", "молодий вигляд", "природний ефект"]
        d.keywords = ["ботокс", "зморшки", "ін'єкції", "молодість", "розгладження"]
        d.contraindications = ["вагітність", "годування груддю", "інфекційні захворювання"]
    elif "філер" in n or "контурн" in n or "belotero" in n or "juvederm" in n or "stylage" in n or "teosyal" in n:
        d.short_description = f"{clean} — контурна пластика або заповнення для корекції об'ємів та форми обличчя."
        d.addresses_problems = ["втрата об'єму", "глибокі зморшки", "хочу збільшити губи", "корекція овалу"]
        d.benefits = ["відновлення об'єму", "корекція форми", "молодий вигляд"]
        d.keywords = ["філер", "контурна пластика", "губи", "об'єм", "зморшки", "корекція"]
        d.contraindications = ["вагітність", "автоімунні захворювання"]
    elif "мезотерап" in n:
        d.short_description = f"{clean} — мезотерапія для глибокого живлення та зволоження шкіри."
        d.addresses_problems = ["зневоднена шкіра", "тьмяність", "дрібні зморшки"]
        d.benefits = ["зволоження", "пружність", "сяйво шкіри"]
        d.keywords = ["мезотерапія", "зволоження", "живлення шкіри", "вітаміни"]
    elif "біоревіт" in n or "rejuran" in n:
        d.short_description = f"{clean} — біоревіталізація для глибокого зволоження та стимуляції вироблення колагену."
        d.addresses_problems = ["зневоднена шкіра", "втрата пружності", "дрібні зморшки"]
        d.benefits = ["глибоке зволоження", "пружність", "профілактика старіння"]
        d.keywords = ["біоревіталізація", "гіалуронова кислота", "зволоження", "колаген"]
    elif "ліфтинг" in n or "rf" in n or "smas" in n:
        d.short_description = f"{clean} — ліфтинг-процедура для підтяжки та зміцнення овалу обличчя."
        d.addresses_problems = ["обвисла шкіра", "другий підборіддя", "втрата пружності"]
        d.benefits = ["підтягнутий овал", "пружність", "молодий вигляд"]
        d.keywords = ["ліфтинг", "підтяжка", "овал обличчя", "пружність"]
    else:
        d.short_description = f"{clean} — професійна косметологічна процедура для здоров'я та краси шкіри обличчя."
        d.addresses_problems = ["потребує догляду шкіра обличчя"]
        d.keywords = [clean.lower(), "обличчя", "догляд", "шкіра"]

    d.sales_pitch = f"Раджу {clean.lower()} для покращення стану шкіри. Косметолог підбере оптимальну програму."
    d.cross_sell = ["альгінатна маска", "домашній догляд"]
    d.target_audience = ["жінки", "чоловіки (для косметології)"]
    return d


def _fill_massage(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — професійний масаж для розслаблення, зняття напруги та покращення самопочуття."
    d.addresses_problems = ["напруга в м'язах", "біль у спині", "стрес", "втома", "хочу розслабитися"]
    d.benefits = ["зняття напруги", "розслаблення", "покращений кровообіг", "гарне самопочуття"]
    d.keywords = ["масаж", "розслаблення", "спина", "шия", "антицелюлітний", "лімфодренажний"]
    d.sales_pitch = "Раджу масаж для зняття напруги та покращення самопочуття. Оберемо зручний час."
    d.cross_sell = ["обгортання", "spa-процедура"]
    d.target_audience = ["всі"]
    return d


def _fill_depilation(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    is_laser = "лазер" in n or "soprano" in n

    if is_laser:
        d.short_description = f"{clean} — лазерна епіляція для тривалого видалення небажаного волосся."
        d.benefits = ["тривалий ефект", "гладка шкіра", "без врослого волосся"]
        d.keywords = ["лазерна епіляція", "лазер", "видалення волосся", "гладка шкіра"]
    else:
        d.short_description = f"{clean} — видалення небажаного волосся воском або шугарингом. Гладкість на 3-4 тижні."
        d.benefits = ["гладкість 3-4 тижні", "тонше волосся після регулярних процедур"]
        d.keywords = ["депіляція", "воск", "шугаринг", "бікіні", "гладка шкіра"]

    d.addresses_problems = ["небажане волосся", "часте гоління", "врослі волоски"]
    d.sales_pitch = f"Раджу {clean.lower()} для тривалої гладкості. Регулярні процедури зменшують ріст волосся."
    d.cross_sell = ["зволожуючий догляд після депіляції"]
    d.target_audience = ["жінки", "чоловіки"]
    return d


def _fill_body(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — процедура для тіла: моделювання, тонус або релакс."
    d.addresses_problems = ["целюліт", "втрата тонусу", "набряки", "зайвий об'єм"]
    d.benefits = ["підтягнута шкіра", "зменшення об'ємів", "тонус"]
    d.keywords = ["тіло", "антицелюліт", "моделювання", "тонус", "обгортання"]
    d.sales_pitch = f"Раджу {clean.lower()} для покращення контурів тіла. Курс 8-10 процедур дає найкращий результат."
    d.cross_sell = ["масаж", "обгортання"]
    d.target_audience = ["жінки"]
    return d


def _fill_podology(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — подологічна процедура для здоров'я нігтів та шкіри стоп."
    d.addresses_problems = ["врослий ніготь", "мозолі", "бородавки", "тріщини", "грибок"]
    d.benefits = ["здорові стопи", "усунення проблеми", "професійний підхід"]
    d.keywords = ["подолог", "врослий ніготь", "мозоль", "стопа", "бородавка", "лікування"]
    d.sales_pitch = "Наш подолог допоможе вирішити проблему професійно та безболісно."
    d.cross_sell = ["медичний педикюр", "ортези"]
    d.target_audience = ["всі"]
    return d


def _fill_cosmetology(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — косметологічна процедура для здоров'я та молодості шкіри."
    d.addresses_problems = ["вікові зміни", "потребує консультації"]
    d.benefits = ["професійний підхід", "індивідуальний план"]
    d.keywords = [clean.lower(), "косметолог", "консультація"]
    d.sales_pitch = "Раджу записатися на консультацію — косметолог оцінить стан шкіри і підбере програму."
    d.cross_sell = ["чистка обличчя", "пілінг"]
    d.target_audience = ["всі"]
    return d


def _fill_generic(name, n, k) -> ProfileData:
    clean = clean_display_name(name)
    d = ProfileData()
    d.short_description = f"{clean} — професійна послуга для краси та догляду."
    d.keywords = [clean.lower()]
    d.sales_pitch = f"Раджу {clean.lower()} для досягнення найкращого результату."
    d.target_audience = ["всі"]
    return d


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

async def amain():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    async with factory() as session:
        # Existing
        sql = sql_text("SELECT canonical_key FROM public.service_profile")
        existing = {row[0] for row in (await session.execute(sql)).fetchall()}

        # Missing
        missing: dict[str, str] = {}  # key → sample_name
        for c in ["ua", "pl", "gb"]:
            sql = sql_text(f"""
                SELECT canonical_key,
                       (array_agg(COALESCE(name_uk, name) ORDER BY name))[1]
                FROM {c}.service WHERE archive=false AND canonical_key IS NOT NULL
                GROUP BY canonical_key
            """)
            for row in (await session.execute(sql)).fetchall():
                if row[0] not in existing and row[0] not in missing:
                    missing[row[0]] = row[1]

        print(f"Missing profiles to fill: {len(missing)}")

        if args.dry_run and not args.apply:
            # Показати sample
            count = 0
            for key, sample in list(missing.items())[:20]:
                data = categorize_and_fill(sample, key)
                print(f"\n{clean_display_name(sample)}")
                print(f"  key: {key}")
                print(f"  desc: {data.short_description[:100]}")
                print(f"  problems: {data.addresses_problems[:3]}")
                print(f"  keywords: {data.keywords[:5]}")
                print(f"  pitch: {data.sales_pitch[:80]}")
                count += 1
            print(f"\n... and {len(missing) - count} more")
            await engine.dispose()
            return

        if not args.apply:
            print("Use --apply to write or --dry-run to preview")
            await engine.dispose()
            return

        repo = ServiceProfileRepository(session, embedder=embedder)
        created = 0
        total = len(missing)

        for i, (key, sample) in enumerate(missing.items(), 1):
            clean = clean_display_name(sample)
            data = categorize_and_fill(sample, key)

            profile = await repo.create(
                canonical_key=key,
                name=clean,
                country=None,
                default_language="uk",
                enabled=True,
                created_by="beauty_expert",
                updated_by="beauty_expert",
            )

            aftercare = data.aftercare_advice
            if isinstance(aftercare, list):
                aftercare = "; ".join(str(x) for x in aftercare) if aftercare else None

            await repo.upsert_translation(
                profile.id, "uk",
                short_description=data.short_description[:500],
                detailed_description=data.short_description,
                addresses_problems=data.addresses_problems,
                target_audience=data.target_audience,
                benefits=data.benefits,
                keywords=data.keywords,
                sales_pitch=data.sales_pitch,
                cross_sell=data.cross_sell,
                procedure_steps=data.procedure_steps,
                contraindications=data.contraindications,
                aftercare_advice=aftercare,
            )

            await repo.save_version(
                profile.id,
                change_summary="Beauty expert auto-fill",
                created_by="beauty_expert",
            )
            created += 1

            if i % 100 == 0:
                print(f"  [{i}/{total}] {created} created...")
                await session.flush()

        await session.commit()
        print(f"\nDONE: {created} profiles created")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
