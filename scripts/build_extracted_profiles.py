#!/usr/bin/env python3
"""
Reads descServ.json (JSONL) and extracts structured profiles for each service.
Outputs extracted_profiles.py with EXTRACTED_PROFILES dict constant.
"""

import json
import re
import textwrap
import os

INPUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'descServ.json')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'extracted_profiles.py')


def clean_service_name(name: str) -> str:
    """Clean multi-line service names, strip whitespace."""
    return re.sub(r'\s+', ' ', name.strip())


def first_sentences(text: str, n: int = 2) -> str:
    """Extract first n sentences from text."""
    if not text:
        return ""
    text = text.replace('\n', ' ').strip()
    text = re.sub(r'\s+', ' ', text)
    # Split on sentence-ending punctuation
    parts = re.split(r'(?<=[.!?])\s+', text)
    result = ' '.join(parts[:n]).strip()
    if len(result) > 500:
        result = result[:497] + '...'
    return result


def extract_problems(desc: str, service: str) -> list:
    """Extract problems this service addresses from description text."""
    problems = []
    desc_lower = desc.lower() if desc else ""

    # Hair-related problems
    problem_patterns = {
        'сухість волосся': [r'сух\w+ волосс', r'зневоднен\w+ волосс'],
        'пошкоджене волосся': [r'пошкоджен\w+ волосс', r'ослаблен\w+ волосс', r'ламк\w+ волосс'],
        'сивина': [r'сивин', r'покриття сивини', r'сивого волосся', r'камуфляж'],
        'посічені кінчики': [r'посічен\w+ кінч', r'посічен\w+ волос'],
        'випадіння волосся': [r'випадіння', r'випаданн', r'алопеці'],
        'пухнастість волосся': [r'пухнаст', r'фріз', r'frizz'],
        'тьмяне волосся': [r'тьмян\w+ волосс', r'без блиск'],
        'пористе волосся': [r'порист\w+ волосс'],
        'неслухняне волосся': [r'неслухнян\w+ волосс'],
        'відросле коріння': [r'корін\w+ відрі', r'відрослий корінь', r'коріння відросло'],
        'небажаний відтінок': [r'небажан\w+ відтін', r'небажан\w+ тон'],
        'темний колір волосся': [r'вихід з\s*темн', r'з чорного', r'зняття кольор'],
        'лупа': [r'лупа', r'лупи'],
        'себорея': [r'себоре'],
        'жирність шкіри голови': [r'жирн\w+ шкір\w+ голов', r'надлишк\w+ себум'],
        'зморшки': [r'зморш', r'мімічн\w+ зморш'],
        'пігментація': [r'пігмент', r'гіперпігмент'],
        'акне': [r'акне', r'вугр', r'прищ'],
        'постакне': [r'постакне', r'сліди\s+акне', r'рубц\w+ після'],
        'втрата пружності шкіри': [r'втрат\w+ пружн', r'в.ял\w+ шкір'],
        'тьмяна шкіра': [r'тьмян\w+ шкір', r'тьмян\w+ кольор\w+ облич'],
        'розширені пори': [r'розширен\w+ пор'],
        'набряклість': [r'набряк', r'набрякл'],
        'целюліт': [r'целюліт', r'апельсинов\w+ шкірк'],
        'біль у спині': [r'біль?\w* у?\s*спин', r'біль?\w* в?\s*шиї'],
        'м\'язова напруга': [r'м.язов\w+ напруг', r'м.язов\w+ спазм'],
        'врослий ніготь': [r'врослий ніг', r'вроста\w+ ніг', r'оніхокриптоз'],
        'грибок нігтів': [r'грибок', r'оніхомікоз', r'мікоз'],
        'натоптиші': [r'натоптиш'],
        'мозолі': [r'мозол'],
        'тріщини на п\'ятах': [r'тріщин\w+.{0,10}п.ят', r'тріщин\w+.{0,10}стоп'],
        'ламкість нігтів': [r'ламк\w+ нігт', r'шарув\w+ нігт'],
        'сухість шкіри рук': [r'сух\w+ шкір\w+ рук'],
        'зневоднена шкіра': [r'зневоднен\w+ шкір', r'зволож'],
        'купероз': [r'купероз', r'судинн\w+ сітк'],
        'нетримання сечі': [r'нетримання сечі'],
        'надмірне потовиділення': [r'гіпергідроз', r'потовиділення'],
        'бруксизм': [r'бруксизм', r'скрегіт зубів'],
        'бородавки': [r'бородавк'],
        'оніхолізис': [r'оніхолізис', r'відшарування нігт'],
    }

    for problem, patterns in problem_patterns.items():
        for pat in patterns:
            if re.search(pat, desc_lower):
                problems.append(problem)
                break

    return problems


def extract_benefits(desc: str) -> list:
    """Extract concrete benefits mentioned in text."""
    benefits = []
    desc_lower = desc.lower() if desc else ""

    benefit_patterns = {
        'блиск волосся': [r'блиск', r'сяйво', r'сяюч'],
        'гладкість': [r'гладк', r'гладеньк'],
        'м\'якість': [r'м.як\w+сть', r'м.яке волосс'],
        'еластичність': [r'еластичн'],
        'зволоження': [r'зволож', r'гідрат'],
        'захист структури волосся': [r'захи\w+ структур', r'зберіга\w+ структур'],
        'стійкий колір': [r'стійк\w+ кольор', r'стійк\w+ ефект', r'стійк\w+ результат'],
        'покриття сивини 100%': [r'покриття сивини на 100', r'зафарбов\w+ 100'],
        'природний вигляд': [r'природн\w+ вигляд', r'натуральн\w+ ефект', r'натуральн\w+ вигляд'],
        'об\'єм': [r'об.єм', r'візуальн\w+ об.єм'],
        'плавний перехід кольору': [r'плавн\w+ перехід'],
        'відновлення структури': [r'відновл\w+ структур', r'реконструк'],
        'зміцнення волосся': [r'зміцн\w+ волосс', r'зміцн\w+ структур'],
        'шовковистість': [r'шовков'],
        'пружність шкіри': [r'пружн\w+ шкір'],
        'ліфтинг-ефект': [r'ліфтинг', r'підтягу'],
        'звуження пор': [r'звуження пор', r'зменш\w+ пор'],
        'вирівнювання тону': [r'вирівн\w+ тон', r'рівн\w+ тон'],
        'зменшення зморшок': [r'зменш\w+ зморш', r'розглад\w+ зморш'],
        'регенерація шкіри': [r'регенера', r'оновлен\w+ шкір'],
        'детоксикація': [r'детокс'],
        'покращення кровообігу': [r'кровообіг', r'мікроциркуляц'],
        'зменшення набряків': [r'зменш\w+ набряк'],
        'розслаблення': [r'розслаблен', r'розслабляюч'],
        'стимуляція росту волосся': [r'стимул\w+ ріст', r'стимул\w+ рост'],
        'протизапальна дія': [r'протизапальн'],
        'антиоксидантний захист': [r'антиоксидант'],
        'захист від УФ': [r'уф-захист', r'uv-захист', r'сонцезахисн', r'ультрафіолет'],
    }

    for benefit, patterns in benefit_patterns.items():
        for pat in patterns:
            if re.search(pat, desc_lower):
                benefits.append(benefit)
                break

    return benefits


def generate_keywords(service: str, desc: str, desc2: str) -> list:
    """Generate Ukrainian keywords a client might use to describe their need."""
    keywords = []
    svc_lower = service.lower()
    full = (desc + ' ' + desc2).lower() if desc2 else desc.lower() if desc else ""

    # Always include the service name cleaned
    clean_name = re.sub(r'\s+', ' ', service.strip().split('\n')[0].strip())
    keywords.append(clean_name)

    # Category-based keywords
    if any(w in svc_lower for w in ['стрижк', 'стріжк']):
        keywords.extend(['стрижка', 'підстригти', 'підстригтися', 'обрізати волосся', 'змінити зачіску'])
        if 'жіноч' in svc_lower:
            keywords.extend(['жіноча стрижка', 'стрижка для жінок', 'модельна стрижка'])
        if 'чубчик' in svc_lower:
            keywords.extend(['чубчик', 'підстригти чубчик', 'чолка'])
        if 'машинк' in svc_lower:
            keywords.extend(['під машинку', 'коротка стрижка', 'чоловіча стрижка машинкою'])
        if 'чоловіч' in svc_lower or 'ножиц' in svc_lower:
            keywords.extend(['чоловіча стрижка', 'стрижка для чоловіків', 'барбер'])

    if any(w in svc_lower for w in ['укладк', 'укладан']):
        keywords.extend(['укладка', 'укласти волосся', 'зробити зачіску', 'висушити волосся'])
        if 'люкс' in svc_lower:
            keywords.extend(['укладка плойкою', 'локони', 'праска'])

    if 'зачіск' in svc_lower:
        keywords.extend(['зачіска', 'святкова зачіска', 'зібрати волосся', 'хвіст', 'пучок'])

    if 'плетін' in svc_lower:
        keywords.extend(['плетіння', 'коса', 'косичка', 'колосок', 'заплести'])

    if 'фарбуван' in svc_lower or 'тонуван' in svc_lower:
        keywords.extend(['фарбування', 'пофарбувати волосся', 'зміна кольору', 'тонування'])
        if 'корен' in svc_lower or 'корін' in svc_lower:
            keywords.extend(['підфарбувати корені', 'фарбування коренів', 'відросле коріння'])
        if 'blonde' in svc_lower or 'блонд' in svc_lower:
            keywords.extend(['блонд', 'освітлення', 'перефарбуватися у блондинку'])

    if 'мелір' in svc_lower or 'melir' in svc_lower:
        keywords.extend(['мелірування', 'висвітлення пасм', 'освітлення пасм'])
        if 'балаяж' in svc_lower or 'balayage' in svc_lower:
            keywords.extend(['балаяж', 'балаяж волосся'])
        if 'шатуш' in svc_lower or 'shatush' in svc_lower:
            keywords.extend(['шатуш', 'ефект вигорілого волосся'])
        if 'airtouch' in svc_lower or 'аіртач' in svc_lower or 'ейртач' in svc_lower:
            keywords.extend(['аіртач', 'airtouch', 'ейртач'])
        if 'highlight' in svc_lower:
            keywords.extend(['хайлайтс', 'мелірування класичне'])

    if 'зняття кольор' in svc_lower or 'знебарвлен' in svc_lower or 'змивк' in svc_lower:
        keywords.extend(['зняття кольору', 'змивка', 'вивести фарбу', 'перефарбуватися зі темного'])

    if any(w in svc_lower for w in ['догляд', 'відновлен', 'реконструк', 'лікуван']):
        keywords.extend(['догляд за волоссям', 'відновлення волосся', 'лікування волосся'])

    if ('кератин' in svc_lower or 'ботокс' in full) and 'бров' not in svc_lower and 'брів' not in svc_lower and 'вій' not in svc_lower and 'вії' not in svc_lower:
        keywords.extend(['кератин', 'випрямлення волосся', 'гладке волосся'])

    if 'нарощ' in svc_lower:
        keywords.extend(['нарощування', 'нарощування волосся', 'додати довжину', 'додати густоту'])

    if 'полірув' in svc_lower:
        keywords.extend(['полірування', 'посічене волосся', 'прибрати посічені кінчики'])

    if 'макіяж' in svc_lower or 'візаж' in svc_lower:
        keywords.extend(['макіяж', 'візаж', 'нафарбуватися', 'мейкап'])
        if 'весіл' in full or 'особлив' in svc_lower:
            keywords.extend(['весільний макіяж', 'святковий макіяж'])
        if 'денн' in svc_lower:
            keywords.extend(['денний макіяж', 'легкий макіяж', 'натуральний макіяж'])
        if 'вечірн' in svc_lower:
            keywords.extend(['вечірній макіяж', 'яскравий макіяж', 'смокі'])

    if 'бров' in svc_lower or 'брів' in svc_lower or 'brow' in svc_lower or 'brwi' in svc_lower:
        keywords.extend(['брови', 'корекція брів', 'фарбування брів', 'оформлення брів'])
        if 'хн' in svc_lower:
            keywords.extend(['хна для брів', 'біотатуаж'])
        if 'ламінуван' in svc_lower:
            keywords.extend(['ламінування брів', 'фіксація брів', 'укладка брів'])
        if 'моделюван' in svc_lower:
            keywords.extend(['моделювання брів', 'форма брів'])
        if 'корекц' in svc_lower:
            keywords.extend(['підкоригувати брови', 'прибрати зайве'])
        if 'воск' in svc_lower:
            keywords.extend(['воскова корекція', 'корекція воском'])

    if 'вій' in svc_lower or 'вії' in svc_lower or 'вій' in full or 'вії' in full:
        keywords.extend(['вії', 'фарбування вій', 'ламінування вій'])

    if 'манікюр' in svc_lower or 'маникюр' in svc_lower:
        keywords.extend(['манікюр', 'нігті', 'гель-лак', 'покриття нігтів'])

    if 'педикюр' in svc_lower or 'педікюр' in svc_lower:
        keywords.extend(['педикюр', 'догляд за стопами', 'обробка ніг'])

    if 'подолог' in svc_lower or 'медичн' in svc_lower:
        keywords.extend(['подолог', 'медичний педикюр', 'лікування нігтів'])

    if 'мезотерап' in svc_lower:
        keywords.extend(['мезотерапія', 'уколи краси', 'вітамінні коктейлі'])

    if 'біоревіталіз' in svc_lower:
        keywords.extend(['біоревіталізація', 'гіалуронова кислота', 'зволоження шкіри уколами'])

    if 'ботулін' in svc_lower or 'ботокс' in svc_lower or 'botox' in svc_lower or 'botul' in svc_lower:
        keywords.extend(['ботокс', 'розгладження зморшок', 'прибрати зморшки'])

    if 'філер' in svc_lower or 'filler' in svc_lower or 'заповнен' in svc_lower or 'моделюван' in svc_lower:
        keywords.extend(['філер', 'контурна пластика', 'збільшення губ', 'заповнення зморшок'])

    if 'пілінг' in svc_lower or 'peeling' in svc_lower:
        keywords.extend(['пілінг', 'очищення шкіри', 'відлущування', 'оновлення шкіри'])

    if 'чистк' in svc_lower:
        keywords.extend(['чистка обличчя', 'очищення пор', 'глибоке очищення'])

    if 'масаж' in svc_lower or 'массаж' in svc_lower:
        keywords.extend(['масаж', 'розслаблення', 'зняти напругу'])
        if 'антицелюліт' in svc_lower:
            keywords.extend(['антицелюлітний масаж', 'прибрати целюліт'])
        if 'спортивн' in svc_lower:
            keywords.extend(['спортивний масаж', 'масаж після тренування'])
        if 'лімфодренаж' in svc_lower:
            keywords.extend(['лімфодренажний масаж', 'зняти набряки'])

    if 'лазерн' in svc_lower or 'laser' in svc_lower:
        keywords.extend(['лазер', 'лазерна процедура'])
        if 'епіляц' in svc_lower:
            keywords.extend(['лазерна епіляція', 'видалення волосся', 'позбутися волосся'])

    if 'депіляц' in svc_lower:
        keywords.extend(['депіляція', 'видалення волосся воском'])

    if 'нитк' in svc_lower or 'niti' in svc_lower or 'aptos' in svc_lower:
        keywords.extend(['нитки для обличчя', 'нитковий ліфтинг', 'підтяжка обличчя'])

    if 'колаген' in svc_lower or 'колаген' in full:
        keywords.extend(['стимулятор колагену', 'омолодження', 'підтяжка шкіри'])

    if 'spa' in svc_lower or 'спа' in svc_lower:
        keywords.extend(['спа', 'спа процедура', 'релакс'])

    if 'афрокудр' in svc_lower:
        keywords.extend(['афрокудрі', 'дрібні кудрі', 'накрутка на плойку'])

    if 'голівудськ' in svc_lower or 'голлівудськ' in svc_lower:
        keywords.extend(['голлівудська хвиля', 'ретро укладка', 'хвилі'])

    if 'завивк' in svc_lower or 'біозавивк' in svc_lower:
        keywords.extend(['завивка', 'кучері', 'локони надовго', 'хімічна завивка'])

    if 'випрямлен' in svc_lower or 'вирівнюван' in svc_lower:
        keywords.extend(['випрямлення волосся', 'розгладження', 'прибрати кучері'])

    if 'борода' in svc_lower or 'вуса' in svc_lower:
        keywords.extend(['борода', 'стрижка бороди', 'вуса'])

    if 'камуфляж' in svc_lower:
        keywords.extend(['камуфляж сивини', 'чоловіче тонування', 'приховати сивину'])

    if 'гіалуронідаз' in svc_lower:
        keywords.extend(['розчинити філер', 'прибрати філер', 'гіалуронідаза'])

    if 'aquapure' in svc_lower or 'гідропілінг' in svc_lower:
        keywords.extend(['гідропілінг', 'апаратна чистка', 'aquapure'])

    if 'icoone' in svc_lower or 'айкун' in svc_lower:
        keywords.extend(['айкун', 'апаратний масаж', 'icoone'])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower not in seen:
            seen.add(kw_lower)
            unique.append(kw)

    # Limit to 8-15
    return unique[:15]


def generate_sales_pitch(service: str, desc: str) -> str:
    """Generate a warm 1-2 sentence recommendation."""
    svc = service.strip().split('\n')[0].strip()
    desc_clean = desc.replace('\n', ' ').strip()[:200] if desc else ""

    # Category-based pitches
    svc_lower = svc.lower()

    if 'стрижк' in svc_lower and 'жіноч' in svc_lower:
        return "Якщо ви давно хотіли оновити свій образ, наші майстри підберуть ідеальну форму стрижки саме під вас, враховуючи структуру волосся та риси обличчя."
    if 'стрижк' in svc_lower and ('чоловіч' in svc_lower or 'ножиц' in svc_lower or 'машинк' in svc_lower):
        return "Наші майстри створять стильний та акуратний образ з урахуванням ваших побажань та особливостей структури волосся."
    if 'укладк' in svc_lower:
        return "Професійна укладка допоможе вам виглядати бездоганно на будь-якій події чи просто порадувати себе доглянутим виглядом."
    if 'зачіск' in svc_lower:
        return "Елегантна зачіска від нашого майстра стане ідеальним завершенням вашого святкового або ділового образу."
    if 'фарбуван' in svc_lower and 'блонд' in svc_lower.lower():
        return "Наші колористи допоможуть досягти чистого та сяючого блонду з максимальним збереженням якості волосся."
    if 'фарбуван' in svc_lower:
        return "Якісне фарбування від наших колористів забезпечить насичений та стійкий колір, зберігаючи здоров'я вашого волосся."
    if 'тонуван' in svc_lower:
        return "Тонування додасть вашому волоссю яскравості та блиску, м'яко скоригувавши відтінок без агресивного впливу."
    if 'мелір' in svc_lower or 'балаяж' in svc_lower or 'шатуш' in svc_lower or 'airtouch' in svc_lower:
        return "Сучасні техніки освітлення створять природний багатовимірний колір з плавними переходами, що чудово виглядає при відростанні."
    if 'зняття кольор' in svc_lower:
        return "Наші досвідчені колористи допоможуть безпечно та поетапно вивести небажаний колір, зберігаючи якість волосся."
    if 'догляд' in svc_lower or 'відновлен' in svc_lower or 'реконструк' in svc_lower:
        return "Професійний догляд допоможе повернути вашому волоссю силу, блиск та здоровий вигляд навіть після інтенсивних процедур."
    if 'нарощ' in svc_lower:
        return "Нарощування волосся додасть бажану довжину та густоту, а наші майстри забезпечать природний та комфортний результат."
    if 'макіяж' in svc_lower:
        return "Професійний макіяж підкреслить вашу природну красу та додасть впевненості для будь-якого заходу."
    if 'бров' in svc_lower:
        return "Правильна форма та колір брів здатні кардинально змінити обличчя, наші майстри підберуть ідеальний варіант саме для вас."
    if 'вій' in svc_lower:
        return "Виразний погляд починається з красивих вій, ця процедура допоможе підкреслити їх без щоденного макіяжу."
    if 'манікюр' in svc_lower:
        return "Доглянуті руки та нігті це завжди стильно, наші майстри забезпечать бездоганний результат та комфорт під час процедури."
    if 'педикюр' in svc_lower:
        return "Професійний педикюр подарує вашим ніжкам легкість та доглянутий вигляд з дотриманням найвищих стандартів гігієни."
    if 'мезотерап' in svc_lower:
        return "Мезотерапія допоможе покращити стан шкіри зсередини, забезпечуючи зволоження, живлення та оновлення на клітинному рівні."
    if 'ботулін' in svc_lower or 'ботокс' in svc_lower or 'botox' in svc_lower:
        return "Ботулінотерапія дозволяє ефективно та безпечно розгладити зморшки, зберігаючи природну міміку обличчя."
    if 'філер' in svc_lower or 'заповнен' in svc_lower or 'моделюван' in svc_lower:
        return "Контурна пластика допоможе повернути обличчю молодий та гармонійний вигляд без хірургічного втручання."
    if 'пілінг' in svc_lower:
        return "Пілінг запустить процеси оновлення шкіри, покращить її текстуру, тон та допоможе вирішити конкретні проблеми."
    if 'чистк' in svc_lower:
        return "Професійна чистка обличчя глибоко очистить пори, покращить кровообіг та поверне шкірі здоровий та свіжий вигляд."
    if 'масаж' in svc_lower or 'массаж' in svc_lower:
        return "Масаж допоможе зняти напругу, покращити кровообіг та подарувати вашому тілу відчуття легкості та відновлення."
    if 'лазер' in svc_lower and 'епіляц' in svc_lower:
        return "Лазерна епіляція забезпечить довготривалий результат видалення небажаного волосся комфортно та безпечно."
    if 'подолог' in svc_lower or 'медичн' in svc_lower:
        return "Кваліфікований подолог допоможе вирішити проблеми зі стопами та нігтями з дотриманням медичних стандартів."
    if 'біоревіталіз' in svc_lower:
        return "Біоревіталізація насичує шкіру гіалуроновою кислотою, повертаючи їй пружність, сяйво та молодий вигляд."
    if 'нитк' in svc_lower or 'aptos' in svc_lower:
        return "Нитковий ліфтинг забезпечить підтяжку без операції, повернувши обличчю чіткий контур та пружність."
    if 'завивк' in svc_lower or 'біозавивк' in svc_lower:
        return "Завивка допоможе створити стійкі локони або хвилі, надаючи волоссю об'єм та текстуру надовго."
    if 'випрямлен' in svc_lower or 'вирівнюван' in svc_lower:
        return "Професійне випрямлення подарує вам гладке, слухняне та блискуче волосся на тривалий час."

    return f"Рекомендуємо цю процедуру для досягнення найкращого результату під керівництвом наших досвідчених фахівців."


def extract_cross_sell(desc: str, desc2: str, service: str) -> list:
    """Extract cross-sell suggestions from text."""
    cross = []
    full = (desc + ' ' + (desc2 or '')).lower()

    cross_patterns = [
        (r'рекоменд\w+\s+тонуванн', 'тонування'),
        (r'тонування не входить', 'тонування (додатково)'),
        (r'додатков\w+ послуг', None),
        (r'рекоменд\w+\s+догляд', 'догляд за волоссям'),
        (r'рекоменд\w+\s+домашн', 'домашній догляд'),
        (r'захист волосся.{0,30}(bond angel|olaplex)', 'захист волосся під час процедури'),
        (r'можна додати', None),
        (r'додається укладка', 'укладка'),
        (r'тонування.{0,30}раз на 3-4 тижн', 'регулярне тонування'),
        (r'корекція.{0,30}через', 'корекція'),
        (r'домашній догляд', 'засоби домашнього догляду'),
        (r'запис\w+ на наступн', 'запис на наступний візит'),
    ]

    for pat, suggestion in cross_patterns:
        if re.search(pat, full):
            if suggestion and suggestion not in cross:
                cross.append(suggestion)

    # Check for explicit cross-sell mentions
    if 'фарбування' in service.lower() and 'тонування' not in service.lower():
        if 'тонування' in full:
            if 'тонування (додатково)' not in cross and 'тонування' not in cross:
                cross.append('тонування')

    return cross[:5]


def extract_procedure_steps(desc: str, desc2: str) -> list:
    """Extract procedure steps if described."""
    steps = []
    full = desc + '\n' + (desc2 or '')

    # Look for numbered steps
    step_matches = re.findall(r'(?:^|\n)\s*(\d+)\.\s*(.+?)(?=\n\s*\d+\.|\n\n|$)', full, re.MULTILINE)
    if step_matches:
        for num, text in step_matches:
            step_text = text.strip().replace('\n', ' ')
            step_text = re.sub(r'\s+', ' ', step_text)
            if len(step_text) > 10:
                steps.append(step_text)

    # Look for "Крок N" steps
    if not steps:
        krok_matches = re.findall(r'Крок\s*(\d+)\s*(.+?)(?=Крок\s*\d+|Примітка|$)', full, re.DOTALL)
        if krok_matches:
            for num, text in krok_matches:
                step_text = text.strip().replace('\n', ' ')
                step_text = re.sub(r'\s+', ' ', step_text)
                if len(step_text) > 10:
                    steps.append(f"Крок {num}: {step_text[:200]}")

    # Look for "У послугу входить:" bullet points
    if not steps:
        entry_match = re.search(r'У послугу входить[:\s]*\n((?:[-*]\s*.+\n?)+)', full)
        if entry_match:
            bullets = re.findall(r'[-*]\s*(.+)', entry_match.group(1))
            steps = [b.strip() for b in bullets if len(b.strip()) > 5]

    return steps[:10]


def extract_contraindications(desc: str, desc2: str) -> list:
    """Extract contraindications ONLY if explicitly mentioned."""
    contras = []
    full = (desc or '') + '\n' + (desc2 or '')

    # Search for explicit contraindication sections
    contra_patterns = [
        r'(?:Протипоказання|ПРОТИПОКАЗАННЯ|ПРОТИВОПОКАЗАНИЯ|противопоказан|Протівопоказання)[:\s]*\n?((?:[-*]\s*.+\n?)+)',
        r'(?:Протипоказання|ПРОТИПОКАЗАННЯ|ПРОТИВОПОКАЗАНИЯ|противопоказан)[:\s]*\n?((?:[^\n]+\n?){1,15})',
    ]
    for pat in contra_patterns:
        contra_match = re.search(pat, full, re.IGNORECASE)
        if contra_match:
            text = contra_match.group(1)
            # Try bullet points first
            items = re.findall(r'[-*]\s*(.+)', text)
            if items:
                for item in items:
                    cleaned = item.strip().rstrip(';').strip()
                    if len(cleaned) > 5 and cleaned.lower() not in ('заборонено:', 'протипоказання:'):
                        contras.append(cleaned)
            else:
                # Try semicolon-separated
                parts = re.split(r'[;\n]', text)
                for part in parts:
                    cleaned = part.strip().rstrip(';').strip()
                    if len(cleaned) > 5 and cleaned.lower() not in ('заборонено:', 'протипоказання:'):
                        contras.append(cleaned)
            if contras:
                break

    # Also look for inline mentions
    if not contras:
        inline_pats = [
            r'(?:Процедуру не можна|заборонено|не рекомендується|Процедуру не можна робити).{0,200}',
            r'(?:СПА заборонено)[:\s]*\n?((?:.+\n?){1,8})',
        ]
        for pat in inline_pats:
            inline = re.search(pat, full, re.IGNORECASE)
            if inline:
                contras.append(inline.group(0).strip()[:200])
                break

    return contras[:10]


def extract_aftercare(desc: str, desc2: str) -> list:
    """Extract aftercare advice ONLY if explicitly present."""
    advice = []
    full = (desc or '') + '\n' + (desc2 or '')

    # Look for aftercare sections
    aftercare_patterns = [
        r'(?:Рекомендації\s+(?:щодо|по)\s+догляд|ПІСЛЯПРОЦЕДУРНИЙ\s+РЕЖИМ|Після\s+процедури|Відновлення\s+після|РЕКОМЕНДАЦІЇ ПІСЛЯ ЛІКУВАННЯ)[:\s]*\n?((?:[-*]\s*.+\n?)+)',
        r'(?:Рекомендації\s+(?:щодо|по)\s+догляд|ПІСЛЯПРОЦЕДУРНИЙ\s+РЕЖИМ|Відновлення\s+після|РЕКОМЕНДАЦІЇ ПІСЛЯ)[:\s]*(.{20,400}?)(?:\n\n|$)',
        r'(?:Після\s+процедури)[:\s]*(.{20,400}?)(?:\n\n|$)',
    ]

    for pat in aftercare_patterns:
        match = re.search(pat, full, re.IGNORECASE)
        if match:
            text = match.group(1)
            items = re.findall(r'[-*]\s*(.+)', text)
            if items:
                advice.extend([i.strip() for i in items if len(i.strip()) > 5])
            elif len(text.strip()) > 10:
                advice.append(text.strip()[:300])
            break

    # Check for specific aftercare patterns
    if not advice:
        aftercare_keywords = [
            r'мити голову можна лише після',
            r'не можна відвідувати саун',
            r'використовувати сонцезахисн',
            r'уникати сонця',
            r'не наносити макіяж',
        ]
        for pat in aftercare_keywords:
            match = re.search(pat + r'.{0,200}', full, re.IGNORECASE)
            if match:
                advice.append(match.group(0).strip()[:200])

    return advice[:5]


def process_entry(entry: dict) -> dict:
    """Process a single JSONL entry into structured profile."""
    service = entry.get('service', '')
    desc = entry.get('description', '')
    desc2 = entry.get('description2', '')

    full_desc = desc + '\n' + desc2 if desc2 else desc

    profile = {
        'short_description': first_sentences(desc, 2),
        'addresses_problems': extract_problems(full_desc, service),
        'benefits': extract_benefits(full_desc),
        'keywords': generate_keywords(service, desc, desc2),
        'sales_pitch': generate_sales_pitch(service, desc),
        'cross_sell': extract_cross_sell(desc, desc2, service),
        'procedure_steps': extract_procedure_steps(desc, desc2),
        'contraindications': extract_contraindications(desc, desc2),
        'aftercare_advice': extract_aftercare(desc, desc2),
    }

    return profile


def format_value(val, indent=2):
    """Format a Python value for pretty-printing."""
    prefix = '    ' * indent
    if isinstance(val, str):
        # Escape for Python string
        escaped = val.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
        return f"'{escaped}'"
    elif isinstance(val, list):
        if not val:
            return '[]'
        items = []
        for item in val:
            items.append(f"{prefix}    {format_value(item, indent + 1)}")
        return '[\n' + ',\n'.join(items) + f',\n{prefix}]'
    elif isinstance(val, dict):
        items = []
        for k, v in val.items():
            formatted_v = format_value(v, indent + 1)
            items.append(f"{prefix}    '{k}': {formatted_v}")
        return '{\n' + ',\n'.join(items) + f',\n{prefix}}}'
    else:
        return repr(val)


def main():
    # Read all entries
    entries = []
    with open(INPUT_PATH, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse line {line_num}: {e}")

    print(f"Read {len(entries)} entries from {INPUT_PATH}")

    # Skip header row if present
    if entries and entries[0].get('service') == 'Послуга':
        entries = entries[1:]
        print("Skipped header row")

    # Process all entries
    profiles = {}
    for entry in entries:
        service = clean_service_name(entry.get('service', ''))
        if not service:
            continue

        profile = process_entry(entry)
        profiles[service] = profile

    print(f"Processed {len(profiles)} services")

    # Write output file
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('"""Extracted service profiles from descServ.json.\n')
        f.write(f'Total services: {len(profiles)}\n')
        f.write('"""\n\n')
        f.write('EXTRACTED_PROFILES = {\n')

        for service_name, profile in profiles.items():
            escaped_name = service_name.replace("'", "\\'")
            f.write(f"\n    '{escaped_name}': {{\n")
            for key, value in profile.items():
                formatted = format_value(value, 2)
                f.write(f"        '{key}': {formatted},\n")
            f.write('    },\n')

        f.write('}\n')

    print(f"Written to {OUTPUT_PATH}")
    print(f"Total profiles: {len(profiles)}")


if __name__ == '__main__':
    main()
