"""Bootstrap script: створює базові ServiceProfile + перший переклад UA.

Вибирає топ-канонічні ключі з реального каталогу і генерує профілі вручну
з типовим контентом beauty індустрії. Beauty-менеджер потім доповнить.

Запуск:
    python -m scripts.seed_profiles
    python -m scripts.seed_profiles --update    # перезаписати існуючі
"""
from __future__ import annotations

import argparse
import asyncio

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory


# 10 bootstrap профілів — реальні canonical keys з нашої БД
PROFILES = [
    {
        "canonical_key": "stryzhka_zhinocha",
        "name": "Стрижка жіноча",
        "translations": {
            "uk": {
                "short_description": "Класична жіноча стрижка з миттям волосся та укладкою на браш.",
                "addresses_problems": [
                    "посічені кінчики", "втрата форми", "відросле волосся",
                    "хочу освіжити стрижку", "потрібна стрижка",
                ],
                "target_audience": ["жінки", "доросле волосся", "будь-яка довжина"],
                "benefits": [
                    "охайна форма", "акуратні кінчики", "освіжений вигляд",
                    "укладка в подарунок",
                ],
                "keywords": [
                    "стрижка", "підстригти", "освіжити", "кінчики", "довжина",
                    "форма", "укладка",
                ],
                "sales_pitch": "Робимо класичну жіночу стрижку з миттям і укладкою. Завжди раджу освіжувати кінчики раз на 2-3 місяці — волосся виглядає здоровішим.",
                "duration_typical_min": 60,
                "cross_sell": ["оформлення брів", "догляд за волоссям"],
            },
        },
    },
    {
        "canonical_key": "manikyur",
        "name": "Манікюр класичний",
        "translations": {
            "uk": {
                "short_description": "Класичний манікюр без покриття: обробка кутикули, форма, шліфування.",
                "addresses_problems": [
                    "хочу манікюр", "охайні нігті", "обробити кутикулу",
                    "сухі задирки", "ламкі нігті",
                ],
                "target_audience": ["всі"],
                "benefits": ["охайні нігті", "доглянуті руки", "відсутність задирок"],
                "keywords": ["манікюр", "нігті", "кутикула", "форма"],
                "sales_pitch": "Класичний манікюр — це базовий догляд: форма, кутикула, шліфування. Без покриття. Якщо хочете триваліше — раджу додати гель-лак.",
                "duration_typical_min": 60,
                "cross_sell": ["гель-лак", "spa-догляд для рук", "оформлення брів"],
            },
        },
    },
    {
        "canonical_key": "gel_lak",
        "name": "Гель-лак",
        "translations": {
            "uk": {
                "short_description": "Покриття гель-лаком тривалістю 2-3 тижні. Великий вибір кольорів.",
                "addresses_problems": [
                    "хочу довговічне покриття", "ламаються нігті",
                    "хочу гарні нігті надовго", "часто облазить лак",
                    "шеллак",
                ],
                "target_audience": ["активні жінки", "хто не хоче часто оновлювати манікюр"],
                "benefits": [
                    "тримається 2-3 тижні", "блиск", "не сколюється",
                    "захищає нігтьову пластину",
                ],
                "keywords": [
                    "гель-лак", "шеллак", "покриття", "довговічно",
                    "блиск", "колір", "лак",
                ],
                "sales_pitch": "Гель-лак тримається 2-3 тижні — найпопулярніший варіант. Робимо разом з манікюром.",
                "duration_typical_min": 90,
                "cross_sell": ["манікюр", "дизайн", "укріплення"],
            },
        },
    },
    {
        "canonical_key": "pedikyur",
        "name": "Педикюр класичний",
        "translations": {
            "uk": {
                "short_description": "Класичний педикюр: обробка стопи, кутикул, шліфування, форма нігтів.",
                "addresses_problems": [
                    "хочу педикюр", "груба шкіра на стопах", "тріщини",
                    "натоптиші", "хочу доглянуті ноги",
                ],
                "target_audience": ["всі"],
                "benefits": [
                    "м'яка шкіра стоп", "охайні нігті", "комфорт у взутті",
                    "відсутність тріщин",
                ],
                "keywords": ["педикюр", "стопи", "ноги", "натоптиші"],
                "sales_pitch": "Класичний педикюр без покриття. Робимо стопи, кутикули, форму. Раджу робити раз на 4-6 тижнів.",
                "duration_typical_min": 60,
                "cross_sell": ["манікюр", "spa догляд для ніг", "гель-лак на ніжки"],
            },
        },
    },
    {
        "canonical_key": "farbuvannya",
        "name": "Фарбування волосся",
        "translations": {
            "uk": {
                "short_description": "Однотонне фарбування волосся на всю довжину професійним барвником.",
                "addresses_problems": [
                    "сива волосся", "тьмяний колір", "хочу змінити колір",
                    "відросли корені", "хочу яскравий колір",
                ],
                "target_audience": ["хто хоче змінити колір", "хто фарбує сивину"],
                "benefits": [
                    "насичений колір", "блиск", "професійний барвник",
                    "тривалий результат",
                ],
                "keywords": [
                    "фарбування", "колір", "пофарбувати", "сивина",
                    "корені", "яскравий",
                ],
                "sales_pitch": "Робимо однотонне фарбування на всю довжину. Майстер підбере відтінок під ваш колір шкіри. Раджу одразу запис на тонування за 3-4 тижні для підтримки.",
                "duration_typical_min": 120,
                "cross_sell": ["тонування", "догляд для фарбованого волосся", "стрижка"],
            },
        },
    },
    {
        "canonical_key": "tonuvannya",
        "name": "Тонування волосся",
        "translations": {
            "uk": {
                "short_description": "Безаміачне тонування для оновлення кольору і блиску. Не пошкоджує волосся.",
                "addresses_problems": [
                    "тьмяний колір після фарбування", "жовтизна на блонді",
                    "хочу освіжити колір", "втрачена насиченість",
                    "виправити рудину",
                ],
                "target_audience": [
                    "блондинки", "після освітлення", "хто хоче освіжити колір без шкоди",
                ],
                "benefits": [
                    "не пошкоджує волосся", "усуває жовтизну",
                    "повертає блиск", "освіжає колір",
                ],
                "keywords": [
                    "тонування", "освіжити", "блонд", "жовтизна",
                    "блиск", "колір", "оновити",
                ],
                "sales_pitch": "Тонування — це делікатна процедура без аміаку. Раджу робити кожні 3-5 тижнів між фарбуваннями. Зберігає здоров'я волосся.",
                "duration_typical_min": 60,
                "cross_sell": ["догляд", "укладка", "стрижка кінчиків"],
            },
        },
    },
    {
        "canonical_key": "korektsiya_briv",
        "name": "Корекція брів",
        "translations": {
            "uk": {
                "short_description": "Корекція форми брів пінцетом або воском. Можна додати фарбування.",
                "addresses_problems": [
                    "неохайні брови", "хочу гарну форму", "брови занадто густі",
                    "брови несиметричні", "хочу архітектуру брів",
                ],
                "target_audience": ["всі"],
                "benefits": [
                    "охайна форма", "природний вигляд", "симетрія",
                    "підкреслює очі",
                ],
                "keywords": ["брови", "корекція", "форма", "пінцет", "віск"],
                "sales_pitch": "Робимо корекцію брів пінцетом або воском. Можу одразу додати фарбування — буде ефект на 2-3 тижні. Хочете?",
                "duration_typical_min": 30,
                "cross_sell": [
                    "фарбування брів", "ламінування брів", "фарбування вій",
                ],
            },
        },
    },
    {
        "canonical_key": "laminuvannya_vii",
        "name": "Ламінування вій",
        "translations": {
            "uk": {
                "short_description": "Ламінування вій з фіксацією форми. Тримається 4-6 тижнів. Без нарощування.",
                "addresses_problems": [
                    "прямі вії", "короткі вії", "хочу довгі вії",
                    "не хочу нарощування", "хочу природний вигляд",
                ],
                "target_audience": [
                    "хто не любить нарощування", "хто хоче природний ефект",
                ],
                "benefits": [
                    "вигин на 4-6 тижнів", "природний ефект", "без щоденного завивання",
                    "не пошкоджує вії",
                ],
                "keywords": [
                    "ламінування", "вії", "вигин", "природні", "довгі",
                ],
                "sales_pitch": "Ламінування вій — це закріплення вигину на 4-6 тижнів. Природний ефект, краса без нарощування. Можу додати фарбування — буде глибший погляд.",
                "duration_typical_min": 60,
                "cross_sell": ["фарбування вій", "корекція брів", "ботокс для вій"],
            },
        },
    },
    {
        "canonical_key": "ukladka",
        "name": "Укладка волосся",
        "translations": {
            "uk": {
                "short_description": "Професійна укладка волосся: браш, локони, прямі або об'ємна.",
                "addresses_problems": [
                    "потрібна укладка", "йду на захід", "фотосесія",
                    "хочу гарно виглядати",
                ],
                "target_audience": [
                    "до особливих подій", "перед заходами", "для фотосесій",
                ],
                "benefits": [
                    "професійний результат", "тримається весь день",
                    "об'єм і блиск",
                ],
                "keywords": [
                    "укладка", "браш", "локони", "об'єм", "захід",
                    "фотосесія", "весілля",
                ],
                "sales_pitch": "Робимо професійну укладку — браш, локони або об'ємну. Раджу одразу запланувати макіяж паралельно — все на одному візиті.",
                "duration_typical_min": 60,
                "cross_sell": ["макіяж", "стрижка", "догляд за волоссям"],
            },
        },
    },
    {
        "canonical_key": "depilyatsiya_glyboke_bikini",
        "name": "Депіляція глибоке бікіні",
        "translations": {
            "uk": {
                "short_description": "Глибока депіляція зони бікіні воском або шугаринг.",
                "addresses_problems": [
                    "потрібна депіляція", "груба щетина", "хочу гладку шкіру",
                    "часто врастають волоски",
                ],
                "target_audience": ["всі"],
                "benefits": [
                    "гладкість на 3-4 тижні", "м'якша щетина після регулярних процедур",
                    "професійний підхід",
                ],
                "keywords": [
                    "депіляція", "бікіні", "глибоке", "віск", "шугаринг",
                ],
                "sales_pitch": "Робимо глибоке бікіні воском. Раджу робити регулярно — щетина стає тоншою з часом. Тримається 3-4 тижні.",
                "duration_typical_min": 30,
                "cross_sell": ["депіляція пахв", "депіляція ніг"],
            },
        },
    },
]


async def amain():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true",
                        help="Перезаписати існуючі профілі")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    created = 0
    updated = 0
    skipped = 0

    try:
        async with factory() as session:
            repo = ServiceProfileRepository(session, embedder=embedder)
            for spec in PROFILES:
                key = spec["canonical_key"]
                existing = await repo.get_by_canonical_key(key)
                if existing and not args.update:
                    skipped += 1
                    print(f"SKIP {key} (exists)")
                    continue

                if existing:
                    profile = existing
                    await repo.update_fields(profile.id, name=spec["name"], updated_by="seed")
                    updated += 1
                    print(f"UPDATE {key}")
                else:
                    profile = await repo.create(
                        canonical_key=key,
                        name=spec["name"],
                        country=None,
                        default_language="uk",
                        created_by="seed",
                        updated_by="seed",
                    )
                    created += 1
                    print(f"CREATE {key}")

                # Translations
                for lang, tr_data in spec["translations"].items():
                    await repo.upsert_translation(profile.id, lang, **tr_data)

                # Initial version snapshot
                await repo.save_version(
                    profile.id,
                    change_summary="Bootstrap seed",
                    created_by="seed",
                )
            await session.commit()
    finally:
        await engine.dispose()

    print(f"\nDONE: {created} created, {updated} updated, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(amain())
