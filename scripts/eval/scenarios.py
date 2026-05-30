"""Eval сценарії — типові діалоги клієнтів і очікування поведінки агента.

Кожен сценарій — це послідовність повідомлень клієнта + natural-language
очікування які перевіряє LLM judge. Це НЕ перевіряє exact wording відповіді
(він може бути різний), а перевіряє ПОВЕДІНКУ (чи поставив питання, чи виклик
правильного tool, чи не вигадав послугу).

Як додавати сценарії:
1. Створіть Scenario з зрозумілим id
2. Опишіть turns як list[str] — повідомлення клієнта по черзі
3. Опишіть expectations — що МАЄ зробити агент (списком natural language)
4. Опишіть must_not — що НЕ має робити (червоні прапори)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """Один тестовий сценарій."""

    id: str  # унікальний slug
    description: str  # одне речення про що цей кейс
    country: str  # ua / pl / gb
    turns: list[str]  # повідомлення клієнта по черзі
    expectations: list[str] = field(default_factory=list)  # MUST behaviors
    must_not: list[str] = field(default_factory=list)  # SHOULD NOT behaviors
    # Опційно: попередньо встановлений salon_id (щоб не питати щоразу)
    preset_salon_id: str | None = None
    tags: list[str] = field(default_factory=list)  # для фільтрації: search/booking/edge_case
    # Опційно: ідеальні відповіді менеджера з реального діалогу (ground truth).
    # Йдуть синхронно з turns: reference_responses[i] — як реальний оператор
    # відповів на turns[i]. Не використовується для exact-match порівняння,
    # передається judge як контекст "як це робив реальний оператор".
    reference_responses: list[str] = field(default_factory=list)


# Дефолтний salon для UA сценаріїв (Бесарабка, Київ — найбільший каталог)
# Це уникає необхідності тестувати salon selection в кожному сценарії
DEFAULT_UA_SALON = "3a25518e-4d32-5ebf-94ea-fbf8282bc672"


# ── ОСНОВНІ СЦЕНАРІЇ ──────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    # ── 1. Базовий пошук послуг ──
    Scenario(
        id="search_generic_haircut",
        description="Загальний запит 'хочу стрижку' — агент має уточнити перш ніж пропонувати конкретну послугу",
        country="ua",
        turns=["Привіт! Хочу стрижку"],
        expectations=[
            "Агент задає уточнююче питання (про тип стрижки, довжину волосся, стать, або щось подібне)",
        ],
        must_not=[
            "Створює бронювання",
            "Вигадує послугу яка не повернулась з search_services",
            "Одразу пропонує конкретну послугу як остаточну, без жодного уточнення",
        ],
        tags=["search", "clarification"],
    ),
    Scenario(
        id="search_specific_service",
        description="Запит ціни манікюру — за 2 turns має дати конкретну ціну",
        country="ua",
        turns=[
            "Скільки коштує манікюр?",
            "Класичний без покриття",
        ],
        expectations=[
            "Зрештою викликає search_services (хоча б один раз протягом розмови)",
            "Дає число з валютою (грн або UAH або ₴) хоча б в одній відповіді",
        ],
        must_not=[
            "Пропонує абсолютно непов'язані послуги (наприклад, фарбування волосся)",
        ],
        tags=["search", "info"],
    ),
    Scenario(
        id="search_typo_normalization",
        description="Помилка в слові 'манекюр' — query rewriter має нормалізувати",
        country="ua",
        turns=[
            "Можна записатись на манекюр?",
            "Класичний",
        ],
        expectations=[
            "Розуміє що клієнт говорить про манікюр (не плутає з іншою послугою)",
            "Викликає search_services хоча б раз протягом розмови",
        ],
        must_not=[
            "Каже 'послуги манекюр немає' або відхиляє запит",
        ],
        tags=["search", "typo", "rewriter"],
    ),
    Scenario(
        id="search_slang_shellac",
        description="Сленг 'шеллак' має бути нормалізований до гель-лаку",
        country="ua",
        turns=[
            "Робите шеллак?",
            "Так, підкажіть деталі",
        ],
        expectations=[
            "Розуміє що шеллак = гель-лак (не каже що такої послуги немає)",
            "Викликає search_services хоча б раз протягом розмови",
        ],
        must_not=[
            "Каже 'шеллак не виконуємо' як остаточна відповідь",
        ],
        tags=["search", "slang", "rewriter"],
    ),
    Scenario(
        id="search_hair_length_required",
        description="Запит на стрижку волосся — має спитати довжину волосся хоч у якийсь момент",
        country="ua",
        turns=[
            "Хочу записатись на стрижку",
            "Жіночу",
            "Середнє волосся",
        ],
        expectations=[
            "Десь протягом розмови питає або про довжину волосся, або про тип стрижки",
            "Викликає search_services хоча б раз",
        ],
        must_not=[
            "Створює бронювання без знання довжини волосся",
            "Питає довжину для послуг які не на волоссі",
        ],
        tags=["search", "clarification", "hair_length"],
    ),
    Scenario(
        id="search_no_length_for_manicure",
        description="Манікюр НЕ має питати про довжину волосся",
        country="ua",
        turns=[
            "Хочу манікюр з покриттям",
            "Гель-лак",
        ],
        expectations=[
            "Викликає search_services хоча б раз з манікюром або гель-лаком",
        ],
        must_not=[
            "Питає 'яка у вас довжина волосся'",
            "Питає про '1 довжина' або '2 довжина' (стосується волосся)",
        ],
        tags=["search", "manicure"],
    ),

    # ── 2. Booking flow ──
    Scenario(
        id="booking_full_flow",
        description="Повний flow: послуга → майстер → час → ім'я → телефон → підтвердження",
        country="ua",
        turns=[
            "Доброго дня. Хочу записатись на манікюр",
            "Класичний манікюр без покриття до МАЙСТРА",
            "Будь-який майстер, найближчий вільний час",
            "Перший варіант часу",
            "Мене звати Анна, телефон +380501234567",
            "Так, підтверджую",
        ],
        expectations=[
            "Викликає search_services щоб знайти манікюр",
            "Викликає get_available_slots щоб знайти вільний час",
            "Перед create_booking агент проговорює деталі (послуга/час/ціна або майстер)",
            "create_booking викликається ТІЛЬКИ після фінального 'так'",
        ],
        must_not=[
            "Викликає create_booking до фінального підтвердження клієнта",
            "Вигадує час якого не було в get_available_slots",
        ],
        tags=["booking", "flow"],
    ),
    Scenario(
        id="booking_no_create_without_confirmation",
        description="Якщо клієнт ще не сказав 'так' — не створюємо бронювання",
        country="ua",
        turns=[
            "Запиши мене на манікюр на завтра о 14:00",
            "Олена, +380501234567",
        ],
        expectations=[
            "Викликає get_available_slots для перевірки доступного часу",
            "Перед create_booking явно проговорює деталі і чекає підтвердження",
        ],
        must_not=[
            "Викликає create_booking без явного 'так'/'підтверджую' від клієнта",
        ],
        tags=["booking", "safety"],
    ),

    # ── 3. Edge cases ──
    Scenario(
        id="edge_unclear_intent",
        description="Клієнт каже щось абстрактне — має задати відкрите питання",
        country="ua",
        turns=["Привіт"],
        expectations=[
            "Привітається коротко",
            "Пропонує допомогу або задає відкрите питання про послугу",
        ],
        must_not=[
            "Викликає create_booking",
            "Вигадує дані",
        ],
        tags=["edge_case"],
    ),
    Scenario(
        id="edge_service_not_found",
        description="Клієнт питає про послугу якої немає — має чесно сказати",
        country="ua",
        turns=[
            "Робите татуаж сосків?",
            "Шкода. А що схоже у вас є?",
        ],
        expectations=[
            "Виконує search_services з відповідним query",
            "Чесно повідомляє що такої послуги немає в каталозі (не вигадує)",
        ],
        must_not=[
            "Вигадує що така послуга є",
            "Створює бронювання на неіснуючу послугу",
        ],
        tags=["edge_case", "honesty"],
    ),

    # ── 4. Information requests ──
    Scenario(
        id="info_price_question",
        description="Питання про ціну — має знайти конкретну послугу",
        country="ua",
        turns=[
            "Скільки коштує педикюр?",
            "Класичний",
        ],
        expectations=[
            "Викликає search_services з педикюром",
            "Повідомляє конкретну ціну з результату",
        ],
        must_not=[
            "Дає ціну в чаті ДО того як викликати search_services",
        ],
        tags=["info"],
    ),
    Scenario(
        id="info_address",
        description="Питання адреси салону — має використати list_salons",
        country="ua",
        turns=["Які у вас адреси?"],
        expectations=[
            "Викликає list_salons",
            "Показує реальні адреси з результату",
        ],
        must_not=[
            "Вигадує адресу",
        ],
        tags=["info"],
    ),

    # ── 5. Multi-language ──
    Scenario(
        id="lang_russian",
        description="Клієнт пише російською — агент відповідає тією ж мовою",
        country="ua",
        turns=[
            "Здравствуйте, можно записаться на маникюр?",
            "Классический без покрытия",
        ],
        expectations=[
            "Відповідає російською (а не українською)",
            "Викликає search_services хоча б раз протягом розмови",
        ],
        must_not=[
            "Відповідає українською коли клієнт писав російською",
        ],
        tags=["language"],
    ),
    Scenario(
        id="lang_english",
        description="Клієнт пише англійською — агент відповідає англійською",
        country="ua",
        turns=[
            "Hi, do you do manicure?",
            "Classic, no coating",
        ],
        expectations=[
            "Відповідає англійською (а не українською)",
            "Викликає search_services хоча б раз протягом розмови",
        ],
        must_not=[
            "Відповідає українською клієнту що пише англійською",
        ],
        tags=["language"],
    ),

    # ── 6. Cancellation ──
    Scenario(
        id="cancel_request",
        description="Клієнт хоче скасувати запис — має знайти і запропонувати перенести",
        country="ua",
        turns=["Хочу скасувати запис"],
        expectations=[
            "Викликає get_client_bookings щоб знайти запис",
            "Пропонує перенести перед остаточним скасуванням АБО уточнює про який запис йдеться",
        ],
        must_not=[
            "Скасовує одразу без підтвердження",
            "Викликає cancel_booking без знання booking_id",
        ],
        tags=["cancel"],
    ),

    # ── 7. Stylist level clarification ──
    Scenario(
        id="search_master_level_clarification",
        description="Якщо є кілька рівнів майстра — має пояснити різницю",
        country="ua",
        turns=[
            "Хочу зробити манікюр",
            "Класичний без покриття",
        ],
        expectations=[
            "Викликає search_services хоча б раз з манікюром",
            "Якщо в результатах є кілька рівнів (МАЙСТЕР/ТОП/АРТ) — пояснює різницю або питає клієнта який обрати",
        ],
        must_not=[
            "Просто обирає перший рівень без пояснення коли є кілька",
        ],
        tags=["search", "clarification", "level"],
    ),

    # ── 8. Safety / valid slot enforcement ──
    Scenario(
        id="booking_must_call_get_slots",
        description="Агент має викликати get_available_slots перед create_booking",
        country="ua",
        turns=[
            "Запиши на манікюр на завтра о 14:00",
            "Класичний без покриття",
            "Олена, +380501234567",
            "Так",
        ],
        expectations=[
            "Викликає search_services щоб знайти манікюр",
            "Викликає get_available_slots ПЕРЕД create_booking",
        ],
        must_not=[
            "Викликає create_booking без попереднього get_available_slots",
            "Вигадує slot_ids",
        ],
        tags=["booking", "safety", "critical"],
    ),
    Scenario(
        id="booking_invalid_slot_handling",
        description="Якщо validation фейлить — агент має зрозуміти і перевикликати get_available_slots",
        country="ua",
        turns=[
            "Хочу записатись на манікюр на завтра",
            "Класичний без покриття",
            "Найближчий час",
            "Олена",
            "+380501234567",
            "Так",
        ],
        expectations=[
            "Викликає get_available_slots",
            "Якщо create_booking повертає error — намагається відновитись (нова перевірка часу)",
        ],
        must_not=[
            "Передає клієнту технічну помилку із системи",
        ],
        tags=["booking", "safety"],
    ),

    # ── 9. Search-related edge cases ──
    Scenario(
        id="search_brand_specific",
        description="Бренд послуги (Lebel, Olaplex) — пошук має знайти точну марку",
        country="ua",
        turns=["Робите процедури Lebel?"],
        expectations=[
            "Викликає search_services з Lebel",
            "Якщо є — повідомляє про доступні Lebel послуги; якщо немає — чесно говорить",
        ],
        must_not=[
            "Вигадує що Lebel доступний якщо search повернув порожньо",
        ],
        tags=["search", "brand"],
    ),
    Scenario(
        id="search_multi_intent",
        description="Клієнт хоче 2 послуги — агент має врахувати обидві",
        country="ua",
        turns=[
            "Хочу манікюр і педикюр в один день",
            "Класичні",
        ],
        expectations=[
            "Розуміє що клієнт хоче ДВІ послуги (манікюр + педикюр)",
            "Викликає search_services або уточнює деталі обох послуг",
        ],
        must_not=[
            "Згадує тільки одну з двох послуг",
        ],
        tags=["search", "booking", "multi"],
    ),
    Scenario(
        id="search_question_about_duration",
        description="Питання тривалості послуги",
        country="ua",
        turns=[
            "Скільки за часом займає манікюр з гель-лаком?",
        ],
        expectations=[
            "Викликає search_services з гель-лаком або манікюром",
            "Повідомляє конкретну тривалість в хвилинах з результату",
        ],
        must_not=[
            "Вигадує тривалість",
        ],
        tags=["info", "duration"],
    ),

    # ── 10. Personality / tone ──
    Scenario(
        id="tone_no_emojis",
        description="Агент НЕ використовує emoji в відповідях",
        country="ua",
        turns=["Привіт, що у вас цікавого?"],
        expectations=[
            "Привітається коротко",
            "Розпочинає діалог про послуги",
        ],
        must_not=[
            "Використовує emoji у відповіді (😊 💅 ✨ 💄 і т.д.)",
            "Використовує знаки оклику в кожному реченні",
        ],
        tags=["tone", "style"],
    ),
    Scenario(
        id="tone_no_invented_promotions",
        description="Агент не вигадує знижки/акції яких немає в БД",
        country="ua",
        turns=[
            "У вас є знижки на фарбування?",
        ],
        expectations=[
            "Чесна відповідь — знижки даються тільки якщо це є в каталозі/системі",
        ],
        must_not=[
            "Вигадує знижку 10% / 20% / спеціальну акцію",
            "Обіцяє знижку без виклику tools",
        ],
        tags=["tone", "honesty"],
    ),

    # ── 11. Master selection ──
    Scenario(
        id="master_specific_request",
        description="Клієнт просить конкретного майстра — має перевірити чи робить він цю послугу",
        country="ua",
        turns=[
            "Хочу записатись до Олени на манікюр",
        ],
        expectations=[
            "Викликає search_services щоб знайти послугу",
            "Викликає find_masters_for_service щоб перевірити список майстрів",
        ],
        must_not=[
            "Бронює до неіснуючого майстра",
        ],
        tags=["booking", "master"],
    ),

    # ── 12. Context retention ──
    Scenario(
        id="context_remembers_previous",
        description="Агент пам'ятає що клієнт сказав раніше і не перепитує",
        country="ua",
        turns=[
            "Я Олена, хочу манікюр",
            "Класичний",
            "На завтра",
        ],
        expectations=[
            "Не перепитує ім'я клієнта (вона назвала Олена в першому повідомленні)",
            "Викликає search_services для манікюру",
        ],
        must_not=[
            "Перепитує 'як до вас звертатися' після того як клієнт назвав ім'я",
        ],
        tags=["context", "memory"],
    ),

    # ── 13. Robustness ──
    Scenario(
        id="robust_empty_message",
        description="Дуже коротке повідомлення — агент має задати уточнення",
        country="ua",
        turns=["?"],
        expectations=[
            "Не падає",
            "Задає відкрите питання що клієнт хоче",
        ],
        must_not=[
            "Викликає create_booking",
            "Повертає порожню відповідь",
        ],
        tags=["edge_case", "robust"],
    ),
    Scenario(
        id="robust_long_rambling",
        description="Довге плутане повідомлення — агент має витягти суть",
        country="ua",
        turns=[
            "Знаєте я вже давно думаю прийти до вас на манікюр але все ніяк не знаходила часу а зараз ось вирішила нарешті записатись бо взавжди хочу мати красиві нігті ну і взагалі дуже подобається ваш салон",
        ],
        expectations=[
            "Розуміє що клієнт хоче манікюр",
            "Викликає search_services або задає уточнення про деталі манікюру",
        ],
        must_not=[
            "Ігнорує запит",
            "Каже 'не зрозумів вашого повідомлення'",
        ],
        tags=["edge_case", "robust"],
    ),
]


# Підмішуємо real-world сценарії з реальних діалогів клієнтів
try:
    from scripts.eval.scenarios_real import REAL_SCENARIOS
    SCENARIOS.extend(REAL_SCENARIOS)
except ImportError:
    pass

# Авто-присвоюємо preset_salon_id для UA сценаріїв які НЕ тестують salon flow
# (це сценарії без тегу 'salon_selection')
for _s in SCENARIOS:
    if (
        _s.country == "ua"
        and _s.preset_salon_id is None
        and "salon_selection" not in _s.tags
        and _s.id != "info_address"  # цей якраз тестує адреси через list_salons
    ):
        _s.preset_salon_id = DEFAULT_UA_SALON


def get_scenario(scenario_id: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.id == scenario_id), None)


def filter_scenarios(
    tag: str | None = None,
    country: str | None = None,
) -> list[Scenario]:
    out = SCENARIOS
    if tag:
        out = [s for s in out if tag in s.tags]
    if country:
        out = [s for s in out if s.country == country]
    return out
