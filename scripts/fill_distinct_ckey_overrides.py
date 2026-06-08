"""Phase 2.5: заповнити per-ckey overrides ТІЛЬКИ для distinct variants.

Distinct variants — ckeys що містять токени-discriminator, які реально
змінюють concerns/target_audience/keywords від profile-defaults.

Detection: token у ckey → apply override template для цього discriminator.

Запуск:
    python -m scripts.fill_distinct_ckey_overrides --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

# Discriminator-based overrides. Token у ckey → дельта-fields що замінюють
# profile defaults.
DISCRIMINATOR_OVERRIDES: list[tuple[re.Pattern, dict]] = [
    # ── Корені (roots only) ───────────────────────────────────────────────
    (re.compile(r"\bkorin\w*\b|\bkoreniv\b|\bkorenya?\b"), {
        "addresses_problems": [
            "відросли корені", "сивина на коренях",
            "потреба у підкрашуванні без зміни довжини",
        ],
        "target_audience": [
            "клієнти що оновлюють корені кожні 4-6 тижнів",
            "ті у кого видно сивину біля коренів",
        ],
        "keywords": [
            "корені", "підкрашування коренів", "фарбування коріння",
            "корекція коренів", "колір біля коренів",
        ],
        "sales_pitch": "Підкрашування коренів — швидке оновлення кольору без втручання у довжину.",
    }),

    # ── Частковий (partial / тільки пальці) ───────────────────────────────
    (re.compile(r"\bchastkovyi\b|\bchastkov\w*\b|\btilky_paltsi\b|\btilky.*paltsi\b"), {
        "addresses_problems": [
            "потрібна обробка лише проблемних зон",
            "не потрібен повний педикюр/манікюр — лише деякі нігті",
        ],
        "target_audience": [
            "клієнти між основними процедурами",
            "ті у кого проблема лише на кількох нігтях",
        ],
        "keywords": [
            "частковий педикюр", "частковий манікюр",
            "тільки пальці", "локальна обробка",
        ],
        "sales_pitch": "Часткова обробка — фокус лише на проблемних зонах, швидко і доступно.",
    }),

    # ── Весільна / особливі події ─────────────────────────────────────────
    (re.compile(r"\bvesilna\b|\bves[iy]lia?\b|\bosoblyv\w*_podii\b|\bdo_osoblyvykh\b"), {
        "addresses_problems": [
            "весілля наближається", "важлива подія потребує особливого образу",
            "хочеться wow-результату на цілий день",
        ],
        "target_audience": [
            "наречені", "клієнтки перед особливою подією",
            "ті хто йде на корпоратив/випускний",
        ],
        "keywords": [
            "весільна", "для нареченої", "до особливої події",
            "для весілля", "корпоратив",
        ],
        "sales_pitch": "Спеціально для важливої події — особлива увага до деталей і стійкість на весь день.",
    }),

    # ── Експрес / швидко ──────────────────────────────────────────────────
    (re.compile(r"\bekspres\w*\b|\bexpres+\w*\b|\bshvyd\w*\b"), {
        "addresses_problems": [
            "обмаль часу на повну процедуру",
            "потрібен швидкий результат до події",
        ],
        "target_audience": [
            "зайняті клієнти у перерві між справами",
            "перед швидкою зустріччю/подією",
        ],
        "keywords": [
            "експрес", "express", "швидко", "за 30 хвилин",
        ],
        "sales_pitch": "Експрес-формат — ефективний результат у короткий час.",
    }),

    # ── Повний / комплекс ─────────────────────────────────────────────────
    (re.compile(r"\bpovnyi\b|\bpovna\b|\bpovne\b|\bkompleks\w*\b|\bcomplex\b"), {
        "addresses_problems": [
            "хочеться комплексного результату за один візит",
            "потрібна повна процедура з усіма етапами",
        ],
        "target_audience": [
            "клієнти що хочуть максимальний ефект",
            "перед особливою подією",
            "ті хто цінує комплексний догляд",
        ],
        "keywords": [
            "комплекс", "повний", "усі етапи", "повна процедура",
        ],
        "sales_pitch": "Комплексна процедура — повний цикл за один візит з максимальним ефектом.",
    }),

    # ── LUX / преміум ─────────────────────────────────────────────────────
    (re.compile(r"\blyuks\w*\b|\blux\b|\bpremium\b|\bpremi\w*\b"), {
        "addresses_problems": [
            "хочеться люкс-результату",
            "потрібна преміум-косметика",
            "важлива подія заслуговує найкращого",
        ],
        "target_audience": [
            "клієнти що цінують преміум-сегмент",
            "перед важливою подією",
        ],
        "keywords": [
            "люкс", "lux", "преміум", "premium",
        ],
        "sales_pitch": "Преміум-формат на люкс-косметиці — максимальний результат і відчуття.",
    }),

    # ── Нарощене волосся / extensions ─────────────────────────────────────
    (re.compile(r"\bnaroshchen\w*\b|\bnaroshchuvann\w*\b|\bextension\w*\b"), {
        "addresses_problems": [
            "потрібен спеціальний догляд за нарощеним волоссям",
            "звичайна укладка може пошкодити капсули",
        ],
        "target_audience": [
            "клієнтки з нарощеним волоссям",
        ],
        "keywords": [
            "нарощене волосся", "нарощування", "капсули",
            "extension", "догляд для нарощеного",
        ],
        "sales_pitch": "Спеціальна техніка для нарощеного волосся — зберігає капсули і дає ідеальний вигляд.",
    }),

    # ── Медичний / лікувальний ────────────────────────────────────────────
    (re.compile(r"\bmedychnyi\b|\blikuvaln\w*\b|\bmedical\b"), {
        "addresses_problems": [
            "медична проблема потребує професійного підходу",
            "звичайна процедура не вирішує проблему",
        ],
        "target_audience": [
            "клієнти з конкретними медичними проблемами",
            "після консультації лікаря",
        ],
        "keywords": [
            "медичний", "лікувальний", "терапевтичний",
        ],
        "sales_pitch": "Медичний формат — професійне розв'язання конкретної проблеми.",
    }),

    # ── Абонемент / курс ──────────────────────────────────────────────────
    (re.compile(r"\babonement\w*\b|\bkurs\b"), {
        "addresses_problems": [
            "потрібен курс процедур для накопичувального ефекту",
            "одиничний візит не дає результату",
        ],
        "target_audience": [
            "клієнти у курсі лікування",
            "ті хто цінує економію при пакетній покупці",
        ],
        "keywords": [
            "абонемент", "курс процедур", "пакет",
        ],
        "sales_pitch": "Курс/абонемент — стабільний результат і економія порівняно з разовими візитами.",
    }),

    # ── Дитяче (kids) ─────────────────────────────────────────────────────
    (re.compile(r"\bdyt\w*\b|\bdytyacha\b|\bkid\w*\b|\bchild\w*\b"), {
        "addresses_problems": [
            "потрібна процедура для дитини",
            "дитина потребує терплячого підходу",
        ],
        "target_audience": [
            "діти", "батьки шукають комфортний салон для дітей",
        ],
        "keywords": [
            "дитяча", "для дітей", "kids",
        ],
        "sales_pitch": "Дитяча процедура — терпляче, акуратно, з ігровим підходом.",
    }),

    # ── Чоловіче (men/чол) - якщо ckey-level, бо profile може бути general ─
    (re.compile(r"\bchol\w*\b|\bcholovich\w*\b|\bmen\b|\bmuzh\w*\b"), {
        "addresses_problems": [
            "чоловік потребує стриманої спеціалізованої процедури",
            "грумінг важливий для бізнес-образу",
        ],
        "target_audience": [
            "чоловіки", "грумінг-клієнти",
        ],
        "keywords": [
            "чоловічий", "для чоловіків", "men",
        ],
        "sales_pitch": "Чоловічий формат — стримана охайна процедура під ваш стиль.",
    }),

    # ── Передпігментація / pre-pigment ────────────────────────────────────
    (re.compile(r"\bperedpigment\w*\b|\bpredpigment\w*\b"), {
        "addresses_problems": [
            "пориста структура волосся погано бере фарбу",
            "пошкоджене волосся потребує підготовки перед фарбуванням",
        ],
        "target_audience": [
            "клієнтки перед основним фарбуванням",
            "ті у кого волосся не тримає колір",
        ],
        "keywords": [
            "передпігментація", "підготовка до фарбування",
        ],
        "sales_pitch": "Передпігментація — підготовка волосся щоб основне фарбування лягло рівно і трималось довше.",
    }),

    # ── Тіло (body) — для масажів частково ────────────────────────────────
    (re.compile(r"\bnig\b|\bnig_paltsi\b|\bpaltsi_nig\b|\bstop\w*\b"), {
        "addresses_problems": [
            "втомлені стопи", "напружені ноги після ходьби",
        ],
        "target_audience": [
            "клієнти що багато ходять/стоять",
        ],
        "keywords": [
            "стопи", "ноги", "масаж ніг", "feet massage",
        ],
        "sales_pitch": "Догляд за ногами — зняття втоми і м'якість стоп.",
    }),

    # ── Антицелюліт ───────────────────────────────────────────────────────
    (re.compile(r"\bantycelyulit\w*\b|\banticelulit\w*\b|\banti.?cellulit\w*\b"), {
        "addresses_problems": [
            "целюліт на стегнах/сідницях",
            "потрібен курс для корекції фігури",
        ],
        "target_audience": [
            "клієнтки що борються з целюлітом",
            "перед сезоном відкритого одягу",
        ],
        "keywords": [
            "антицелюліт", "anti-cellulite", "корекція фігури",
        ],
        "sales_pitch": "Антицелюлітна процедура — курс для розгладження шкіри і корекції фігури.",
    }),

    # ── Релакс / спа ──────────────────────────────────────────────────────
    (re.compile(r"\brelaks\w*\b|\brelax\b|\bspa\b"), {
        "addresses_problems": [
            "стрес і втома потребують релаксу",
            "хочеться SPA-ритуалу",
        ],
        "target_audience": [
            "клієнти зі стресом", "ті хто шукає ритуал релакса",
        ],
        "keywords": [
            "релакс", "spa", "розслабляючий", "ритуал",
        ],
        "sales_pitch": "Релакс-формат — глибока релаксація з ароматерапією і м'якими техніками.",
    }),

    # ── Косметологічні sub-types (anti-ox/collagen/oxygen/etc.) ──────────
    (re.compile(r"\bantyoksydant\w*\b|\bantyoks\w*\b|\bantioxid\w*\b"), {
        "addresses_problems": [
            "вплив вільних радикалів і екології на шкіру",
            "тьмяний колір обличчя через стрес/міське середовище",
            "ранні ознаки фотостаріння",
        ],
        "target_audience": [
            "клієнти у місті з поганою екологією",
            "ті хто проводить багато часу за екраном",
            "після відпусток зі сонячним опроміненням",
        ],
        "keywords": [
            "антиоксидант", "antioxidant", "захист від вільних радикалів",
            "antioxidant care",
        ],
        "sales_pitch": "Антиоксидантний догляд — захист шкіри від вільних радикалів і стресу міста.",
    }),
    (re.compile(r"\bkolagen\w*\b|\bcollagen\w*\b"), {
        "addresses_problems": [
            "втрата пружності шкіри",
            "перші вікові зміни",
            "тонкі зморшки",
        ],
        "target_audience": [
            "клієнтки 30+ що профілактують вікові зміни",
            "ті хто хоче відновити пружність шкіри",
        ],
        "keywords": [
            "колаген", "collagen", "пружність шкіри",
            "відновлення колагену",
        ],
        "sales_pitch": "Колагеновий догляд — стимуляція власного колагену для пружної молодої шкіри.",
    }),
    (re.compile(r"\bkysn\w*\b|\boxygen\b"), {
        "addresses_problems": [
            "тьмяна шкіра без свіжості",
            "набряки і застій",
            "недостатнє дихання шкіри",
        ],
        "target_audience": [
            "клієнтки з тьмяним кольором обличчя",
            "ті хто живе в містах з поганою екологією",
        ],
        "keywords": [
            "кисневий", "oxygen", "оксиген", "оксигенація",
        ],
        "sales_pitch": "Кисневий догляд — насичення шкіри киснем для свіжого здорового вигляду.",
    }),
    (re.compile(r"\bosvitlyu\w*\b|\bosvitl\w*\b|\bbrigh\w*\b|\bwhitenin\w*\b"), {
        "addresses_problems": [
            "пігментні плями",
            "нерівний тон обличчя",
            "сонячне пігментування після відпусток",
        ],
        "target_audience": [
            "клієнтки з пігментацією",
            "після літа з сонячним опроміненням",
        ],
        "keywords": [
            "освітлення шкіри", "освітлювальний догляд",
            "пігментні плями", "brightening",
        ],
        "sales_pitch": "Освітлювальний догляд — вирівнює тон і освітлює пігментні плями.",
    }),
    (re.compile(r"\bzhyvlen\w*\b|\bnutri\w*\b"), {
        "addresses_problems": [
            "сухість і виснаженість шкіри",
            "втрата живості шкіри взимку",
            "потреба у інтенсивному живленні",
        ],
        "target_audience": [
            "клієнти зі суxою шкірою",
            "взимку коли шкіра потребує живлення",
        ],
        "keywords": [
            "живлення", "поживний", "nutrition",
            "живильний догляд",
        ],
        "sales_pitch": "Живильний догляд — інтенсивне живлення шкіри потужними активними компонентами.",
    }),
    (re.compile(r"\bzvolozhen\w*\b|\bhydrat\w*\b|\bmoist\w*\b"), {
        "addresses_problems": [
            "зневоднена шкіра",
            "відчуття стянутості",
            "тонкі зморшки зневоднення",
        ],
        "target_audience": [
            "клієнти з сухою/зневодненою шкірою",
            "після перельотів, кондиціонерів",
        ],
        "keywords": [
            "зволоження", "hydration", "moisturizing",
            "глибоке зволоження",
        ],
        "sales_pitch": "Зволожуючий догляд — глибоке відновлення водного балансу шкіри.",
    }),
    (re.compile(r"\bpid_ochi\b|\bochi\b|\bnabryak\w*\b"), {
        "addresses_problems": [
            "набряки під очима",
            "темні кола",
            "тонкі зморшки навколо очей",
        ],
        "target_audience": [
            "клієнтки з ознаками втоми на обличчі",
            "ті хто прокидається з мішками під очима",
        ],
        "keywords": [
            "догляд під очі", "набряки під очима",
            "темні кола", "зона навколо очей",
        ],
        "sales_pitch": "Догляд для зони навколо очей — зняття набряків і освітлення темних кіл.",
    }),
    (re.compile(r"\bspyna\b|\bback\b"), {
        "addresses_problems": [
            "акне на спині",
            "забиті пори на спині",
            "перед сезоном відкритого одягу",
        ],
        "target_audience": [
            "клієнти з проблемною шкірою спини",
            "перед літом/відпусткою",
        ],
        "keywords": [
            "чистка спини", "догляд за спиною",
            "акне на спині",
        ],
        "sales_pitch": "Чистка/догляд для спини — глибоке очищення часто забутої але важливої зони.",
    }),
    (re.compile(r"\bdekolte\b|\bdécolleté\b"), {
        "addresses_problems": [
            "зона декольте втрачає пружність раніше за обличчя",
            "пігментація на декольте",
        ],
        "target_audience": [
            "клієнтки що піклуються про шию і декольте",
        ],
        "keywords": [
            "декольте", "шия", "neck", "décolleté",
        ],
        "sales_pitch": "Догляд за зоною декольте — підтримка пружності там де шкіра старіє швидше.",
    }),
    (re.compile(r"\bampul\w*\b|\bampoule\b"), {
        "addresses_problems": [
            "потреба у концентрованих активних компонентах",
            "інтенсивний курс лікування волосся/шкіри",
        ],
        "target_audience": [
            "клієнти у курсі лікування",
            "ті хто хоче максимально активний догляд",
        ],
        "keywords": [
            "ампула", "ampoule", "концентрат",
        ],
        "sales_pitch": "Ампульний догляд — концентровані активи для максимального лікувального ефекту.",
    }),
    (re.compile(r"\bmaska\b|\bmask\b"), {
        "addresses_problems": [
            "потреба у інтенсивному додатковому догляді",
            "шкіра/волосся вимагає бустеру після основної процедури",
        ],
        "target_audience": [
            "клієнти що цінують комплексний догляд",
        ],
        "keywords": [
            "маска", "mask", "поживна маска",
        ],
        "sales_pitch": "Маска — додатковий інтенсивний догляд для максимального результату.",
    }),
    (re.compile(r"\bskrab\b|\bckrab\b|\bscrub\b|\bpiling\w*\b"), {
        "addresses_problems": [
            "огрубіла шкіра потребує оновлення",
            "нерівна текстура",
            "потрібне очищення мертвих клітин",
        ],
        "target_audience": [
            "клієнти з нерівним рельєфом шкіри",
        ],
        "keywords": [
            "скраб", "пілінг", "scrub", "ексфоліація",
        ],
        "sales_pitch": "Скраб/пілінг — оновлення шкіри через відлущення мертвих клітин.",
    }),
    (re.compile(r"\btermolif\w*\b|\bthermolif\w*\b|\btermo\b|\bnir\b|\baft\b"), {
        "addresses_problems": [
            "втрата контурів обличчя",
            "опущення тканин",
            "потреба у безоперативному ліфтингу",
        ],
        "target_audience": [
            "клієнтки 35+ з ознаками опущення тканин",
        ],
        "keywords": [
            "термоліфтинг", "thermal lifting", "NIR", "AFT",
        ],
        "sales_pitch": "Термоліфтинг — апаратний ліфтинг тканин без хірургії.",
    }),
    (re.compile(r"\bultrazvuk\w*\b|\bultraso\w*\b"), {
        "addresses_problems": [
            "потрібне неінвазивне очищення",
            "забиті пори",
        ],
        "target_audience": [
            "клієнти що бояться механічної чистки",
        ],
        "keywords": [
            "ультразвук", "ultrasound", "УЗ-чистка",
        ],
        "sales_pitch": "Ультразвуковий формат — неінвазивне очищення без болю.",
    }),
    (re.compile(r"\bvakuum\w*\b|\bvacuum\b|\bgidropil\w*\b|\bgidro\w*\b"), {
        "addresses_problems": [
            "забиті пори потребують delicate очищення",
            "потреба у комплексній чистці+зволоження",
        ],
        "target_audience": [
            "клієнти що цінують apparat-чистку",
        ],
        "keywords": [
            "вакуумна чистка", "гідрочистка", "гідропілінг",
            "AquaPure", "hydro peel",
        ],
        "sales_pitch": "Вакуумна/гідрочистка — глибоке делікатне очищення апаратом.",
    }),
    (re.compile(r"\bmekhanichn\w*\b|\bmechanical\b"), {
        "addresses_problems": [
            "забиті пори потребують глибокого ручного очищення",
            "чорні крапки",
        ],
        "target_audience": [
            "клієнти з проблемною/жирною шкірою",
        ],
        "keywords": [
            "механічна чистка", "ручна чистка",
            "mechanical cleansing",
        ],
        "sales_pitch": "Механічна чистка — ручне глибоке очищення пор від чорних крапок.",
    }),
    (re.compile(r"\bdetoks\b|\bdetox\b"), {
        "addresses_problems": [
            "потреба у детоксі організму",
            "застій метаболізму",
        ],
        "target_audience": [
            "клієнти після переїдання/свят",
            "ті хто корегує фігуру",
        ],
        "keywords": [
            "детокс", "detox", "очищення організму",
        ],
        "sales_pitch": "Детокс-програма — очищення організму і прискорення метаболізму.",
    }),
    (re.compile(r"\bkarbox\w*\b|\bcarbo\w*\b|\bco2\b"), {
        "addresses_problems": [
            "тьмяний колір обличчя",
            "набряки", "темні кола під очима",
        ],
        "target_audience": [
            "клієнтки 30+ з ознаками втоми",
        ],
        "keywords": [
            "карбоксітерапія", "CO2 терапія", "карбокс",
        ],
        "sales_pitch": "Карбоксітерапія — оксигенація шкіри для свіжого кольору обличчя.",
    }),
    (re.compile(r"\bgolova\b|\bgolovi\b|\bskalp\w*\b|\bshkiry_golovy\b"), {
        "addresses_problems": [
            "лупа", "сверблячка шкіри голови",
            "жирне коріння", "втрата волосся",
        ],
        "target_audience": [
            "клієнти з проблемами шкіри голови",
        ],
        "keywords": [
            "пілінг голови", "догляд шкіри голови",
            "scalp peeling", "лікування голови",
        ],
        "sales_pitch": "Догляд за шкірою голови — основа здорового росту і блиску волосся.",
    }),
    (re.compile(r"\bfrench\b|\bfrench_man\w*\b"), {
        "addresses_problems": [
            "хочеться класичного френч-дизайну",
            "виразний акцент на нігтях",
        ],
        "target_audience": [
            "клієнтки що люблять класичний нігтьовий дизайн",
        ],
        "keywords": [
            "френч", "french", "класичний дизайн нігтів",
        ],
        "sales_pitch": "Френч — класичний дизайн що ніколи не виходить з моди.",
    }),
    (re.compile(r"\bfolga\b|\bfoil\b|\bstemp\w*\b|\b3d\b"), {
        "addresses_problems": [
            "хочеться оригінального дизайну нігтів",
            "потрібен акцент до події",
        ],
        "target_audience": [
            "клієнтки що люблять складний нігтьовий дизайн",
        ],
        "keywords": [
            "фольга", "стемпінг", "3D дизайн",
            "акцент на нігтях",
        ],
        "sales_pitch": "Складний дизайн (фольга/стемпінг/3D) — індивідуальний акцент для wow-ефекту.",
    }),
    (re.compile(r"\bblysk\w*\b|\bshine\b"), {
        "addresses_problems": [
            "тьмяне волосся",
            "потреба у блиску після фарбування/освітлення",
        ],
        "target_audience": [
            "клієнтки що хочуть дзеркального блиску волосся",
        ],
        "keywords": [
            "блиск", "shine", "сяюча укладка",
        ],
        "sales_pitch": "Догляд для блиску — дзеркальне сяяння волосся після процедури.",
    }),
    (re.compile(r"\bobyem\w*\b|\bvolume\b"), {
        "addresses_problems": [
            "тонке плоске волосся без об'єму",
        ],
        "target_audience": [
            "клієнтки з тонким волоссям",
        ],
        "keywords": [
            "об'єм волосся", "volume", "пишна укладка",
        ],
        "sales_pitch": "Догляд/укладка для об'єму — пишне здорове волосся.",
    }),
    (re.compile(r"\babsolyutne\b|\babsolute\b|\bshchasty\w*\b|\bhappi\w*\b"), {
        "addresses_problems": [
            "сильно пошкоджене волосся потребує реанімації",
            "після агресивних процедур",
        ],
        "target_audience": [
            "клієнтки з критично пошкодженим волоссям",
            "після невдалого фарбування/освітлення",
        ],
        "keywords": [
            "абсолютне щастя", "absolute happiness",
            "відновлення критично пошкодженого",
        ],
        "sales_pitch": "Абсолютне щастя для волосся — інтенсивна реанімація сильно пошкодженого волосся.",
    }),
    (re.compile(r"\baprez\b|\bpost\w*\b|\baftercare\b"), {
        "addresses_problems": [
            "догляд після процедури",
        ],
        "target_audience": [
            "клієнти що завершують основний курс",
        ],
        "keywords": [
            "догляд після", "aftercare", "пост-процедурний",
        ],
        "sales_pitch": "Догляд після процедури — підтримка результату і прискорене відновлення.",
    }),
]


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--language", default="uk")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Збираємо всі (profile_id, ckey) з canonical_keys
            r = await session.execute(text(f"""
                SELECT sp.id, sp.name, sp.canonical_keys,
                       COALESCE(t.ckey_overrides, '{{}}'::jsonb) AS existing_overrides
                FROM public.service_profile sp
                LEFT JOIN public.service_profile_translation t
                  ON t.profile_id = sp.id AND t.language = :lang
                WHERE sp.country = :c
            """), {"c": args.country, "lang": args.language})

            profiles_data = []
            for pid, pname, ckeys, existing in r.all():
                profiles_data.append((str(pid), pname, list(ckeys or []), dict(existing or {})))

            # Для кожного ckey знайти discriminator(и) → побудувати override
            to_update: dict[str, dict] = defaultdict(dict)  # profile_id → ckey → override
            matched_count = 0
            for pid, pname, ckeys, existing in profiles_data:
                for ck in ckeys:
                    if ck in existing:
                        continue  # уже override exists
                    merged: dict = {}
                    for pat, override in DISCRIMINATOR_OVERRIDES:
                        if pat.search(ck):
                            # Merge поля з override у merged (overwriting нижчі-приорітет)
                            for f, v in override.items():
                                if f not in merged:
                                    merged[f] = v
                    if merged:
                        to_update[pid][ck] = merged
                        matched_count += 1

            print(f"[{args.country}] profiles scanned: {len(profiles_data)}")
            print(f"[{args.country}] distinct-variant ckeys matched: {matched_count}")
            for pid, overrides in list(to_update.items())[:5]:
                pname = next(x[1] for x in profiles_data if x[0] == pid)
                print(f"  Profile '{pname}': {len(overrides)} overrides")
                for ck in list(overrides.keys())[:3]:
                    print(f"    + {ck[:60]}")

            if not args.apply:
                print("\nDRY RUN. Use --apply.")
                return

            updated = 0
            for pid, new_overrides in to_update.items():
                existing = next(x[3] for x in profiles_data if x[0] == pid)
                merged_all = {**existing, **new_overrides}
                # Перевірити translation існує
                t_row = (await session.execute(text(
                    "SELECT id FROM public.service_profile_translation "
                    "WHERE profile_id=:pid AND language=:lang"
                ), {"pid": pid, "lang": args.language})).first()
                if not t_row:
                    continue  # skip — translation відсутній, fill_profile_content створює
                await session.execute(text("""
                    UPDATE public.service_profile_translation
                    SET ckey_overrides = CAST(:ov AS jsonb),
                        updated_at = NOW()
                    WHERE profile_id = :pid AND language = :lang
                """), {
                    "ov": json.dumps(merged_all, ensure_ascii=False),
                    "pid": pid, "lang": args.language,
                })
                updated += 1
            await session.commit()
            print(f"\n[{args.country}] UPDATED {updated} translations з {matched_count} ckey-overrides.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
