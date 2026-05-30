"""Категоризатор canonical_keys → ~80 categories на основі service.name.

CATEGORY_RULES — список tuples (category_id, sample_name_regex).
Правила застосовуються по порядку: перший матч виграє.

Вхід: .logs/canonical_keys_analysis.json
Вихід: .logs/canonical_keys_categorized.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

INPUT = Path(__file__).parent.parent / ".logs" / "canonical_keys_analysis.json"
OUTPUT = Path(__file__).parent.parent / ".logs" / "canonical_keys_categorized.json"


# (category_id, [list of regex patterns to match against ANY sample name])
# Перший pattern що матчиться — виграє. Тому специфічніші — ВИЩЕ за загальні.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    # ⚠ ПОРЯДОК КРИТИЧНИЙ: специфічні правила (бренд/техніка/процедура)
    # ЗАВЖДИ перед загальними (UKLADKA, ZACHISKA, MANIKYUR fallback)
    # бо назви послуг часто містять "укладка" як етап (наприклад "Lebel щастя + укладка")

    # === STRIZHKA (специфічні стрижки) ===
    ("STRYZHKA_CHUBCHYK", [r"стрижка\s+чубчик"]),
    ("STRYZHKA_CHOLOVICHA", [r"чоловіч.*стрижк|стрижк.*чоловіч|barber|cholovich.*stryzhk|stryzhk.*cholovich"]),
    ("STRYZHKA_DYTYACHA", [r"дитяч.*стрижк|стрижк.*дитяч|для\s+дітей"]),
    ("BORODA", [r"\bбород|\bbeard|вуса"]),

    # === ZACHISKA (специфічні зачіски) ===
    ("ZACHISKA_VESILNA", [r"весільн.*зачіск|зачіск.*весільн|wedding"]),
    ("ZACHISKA_VECHIRNYA", [r"зачіск|chignon|святков.*зачіск"]),

    # === MELIRUVANNYA / OSVITLENNYA — спочатку, бо часто йде з тонуванням/укладкою ===
    ("MELIRUVANNYA_AIRTOUCH", [r"airtouch|ейртач|аіртач"]),
    ("MELIRUVANNYA_HANDTOUCH", [r"handtouch|хендтач|хандтач"]),
    ("MELIRUVANNYA_BALAYAZH", [r"балаяж|balayage|balayaz"]),
    ("MELIRUVANNYA_SHATUSH", [r"шатуш|shatush"]),
    ("MELIRUVANNYA_BABY_LIGHTS", [r"baby.?lights|babylights|hightlights|highlights"]),
    ("MELIRUVANNYA_VUAL", [r"вуаль|veil"]),
    ("MELIRUVANNYA_KONTURYNG", [r"контуринг|contour"]),
    ("MELIRUVANNYA_DIAGONAL", [r"діагональ|diagonal"]),
    ("MELIRUVANNYA_SONYACHNYI", [r"сонячн.*ефект|sunny|sun.*effect"]),
    ("MELIRUVANNYA_KLASYCHNE", [r"мелірув|меліров|освітлен|melir|melyr|освітлюючий\s+крем|освітлюючі\s+масла|выход\s+из\s+чорн|вихід\s+з\s+чорн|з\s+чорного|осветл"]),

    # === BRAND-SPECIFIC DOGLYAD VOLOSSYA — спочатку, бо часто йдуть з укладкою ===
    ("ABSOLUTNE_SHCHASTYA_LEBEL", [r"absolyutne|щастя\s+для\s+волос|happiness.*lebel|lebel.*happi|щасливе\s+фарбув"]),
    ("INKARAMI_TOKIO", [r"інкарамі|inkarami"]),
    ("DOGLYAD_LEBEL", [r"\blebel\b|лебел"]),
    ("DOGLYAD_BRAE_POWER_DOSE", [r"brae.*power|power.*dose"]),
    ("DOGLYAD_BRAE_BOND_ANGEL", [r"bond.*angel|brae.*bond"]),
    ("DOGLYAD_BRAE", [r"\bbrae\b|brae"]),
    ("DOGLYAD_HADAT", [r"hadat"]),
    ("DOGLYAD_MILBON_CRONNA", [r"milbon|cronna"]),
    ("DOGLYAD_ORIBE", [r"oribe"]),
    ("DOGLYAD_DR_SORBIE", [r"dr\.?\s*sorbie|sorbie"]),
    ("DOGLYAD_BALMAIN_HAIR", [r"\bbalmain.*(?:hair|укладк|догляд)|hair.*balmain"]),
    ("DOGLYAD_KEUNE_HAIR", [r"keune"]),
    ("DOGLYAD_LA_BIOSTHETIQUE_HAIR", [r"la\s*bios|labiost|біостетік|biost.*hair"]),
    ("DOGLYAD_ORISING", [r"orising|orsing"]),
    ("DOGLYAD_REVIVAL", [r"revival"]),
    ("DOGLYAD_AWAPUHI", [r"awapuhi"]),
    ("DOGLYAD_AMPULA", [r"ампул|ampoule|ampuł|amp\b"]),
    ("DOGLYAD_KERATIN", [r"керат|keratin"]),
    ("DOGLYAD_BOTOKS_VOLOSSYA", [r"botox.*волос|ботокс.*волос|hair.*botox"]),
    ("DOGLYAD_REKONSTRUKTSIYA", [r"реконструк|reconstruction"]),
    ("DOGLYAD_LAMINUVANNYA_VOLOSSYA", [r"ламінув.*волос|hair.*laminat"]),
    ("DOGLYAD_ZAKHYST", [r"захист.*волос|protect.*hair|pcc|bond.*protect"]),
    ("DOGLYAD_K18", [r"k18|к18"]),
    ("DOGLYAD_OLAPLEX", [r"olaplex"]),
    ("DOGLYAD_PROTY_LUPY", [r"проти\s+лупи|antidandruff|перхот"]),
    ("DOGLYAD_INFENOM", [r"infenom|инфеном"]),
    ("DOGLYAD_LUXURY_ICE", [r"luxury\s*ice|кри[жз]ан.*сяй"]),
    ("DOGLYAD_SVICHKA_SPA", [r"свічка.*spa|spa.*свічка|candle\s*spa"]),
    ("DOGLYAD_ZMITSNENNYA_VOLOSSYA", [r"зміцнен.*волос|укріплен.*волос|hair.*strength"]),
    ("DOGLYAD_TAMPONADA", [r"тампонад"]),
    ("DOGLYAD_VOLOSSYA", [r"догляд.*волос|відновлен.*волос|маска.*волос|курс.*волос|hair.*care|treatment|hair.*mask|стайлінг.*лак|спрей.*для\s*волос|мус.*для\s*волос|hair\s*styling|spray"]),

    # === FARBUVANNYA / KOLIR ===
    ("FARBUVANNYA_KORENI", [r"фарбуван.*коре|коре.*фарбуван|підфарбуван.*коре"]),
    ("TOTAL_BLOND", [r"total\s*blond|тотал.*блонд|повне\s*освітлення"]),
    ("BLOND_MYTTYA", [r"блонд.?мит|освітлен.*мит"]),
    ("FARBUVANNYA_BRIV", [r"фарбуван.*брів|brow.*color"]),
    ("KHNA_BRIV", [r"хна.*брів|biotatuazh|biotattoo|biotaty|пудров.*брів|пудра.*бр"]),
    ("FARBUVANNYA_VII", [r"фарбуван.*вій|lash.*tinting|colorize.*lash"]),
    ("KAMUFLYAZH_SYVYNY", [r"камуфляж|камуфлюв|сед.?тон|приховат.*сив|чоловіч.*тонув"]),
    ("PEREDPIGMENTATSIYA", [r"передпігмент|перепігмент|prepigm|предпігмен"]),
    ("ZNYATTYA_KOLORU", [r"зняття.*кольор|вивед.*кольор|змивк|видален.*кольор|color\s*removal|décap|органічн.*змивк|зняти.*колір|нейтралізаці.*кольор"]),
    ("ZNYATTYA_GEL_LAK", [r"зняття.*гель|зняти.*гель|зняти\s*shellac|зняття.*шеллак|зняти.*покритт.*ніг"]),
    ("ZNYATTYA_NAROSHCHENYKH_NIGTIV", [r"зняття.*нарощ.*нігт"]),
    ("ZNYATTYA_VII", [r"зняття.*вій|video.*lash"]),
    ("ZNYATTYA_NAROSHCHENOGO_VOLOSSYA", [r"зняття.*нарощ.*волос"]),
    ("TONUVANNYA", [r"тонуван|color\s*gloss|toning|відтінок\s+shine|shine.?tone"]),
    ("FARBUVANNYA", [r"фарбуван|пофарбу|crom|hair\s*color|coloring"]),

    # === UKLADKA / hair-styling (FALLBACK — після specific) ===
    ("AFROKUDRI", [r"афро.?накрут|афро.?кудр|афрокудр|афрокуч|afrolocon"]),
    ("UKLADKA_LOKONY", [r"гол.*івуд.*хвиля|locon|укладка.*локон|hollywood\s*wave|накрутк|локони|лок_оны"]),
    ("UKLADKA_NAROSHCHENOGO", [r"укладк.*нарощ|нарощ.*укладк"]),
    ("UKLADKA_LYUKS", [r"укладк.*люкс|люкс.*укладк|кераст\s*лю|лю\s*кераст"]),
    ("UKLADKA", [r"укладк|укладан|brushing|styling"]),
    ("PLETINNYA", [r"плет|коса|колосок|корн.?роуз|зар?брана\s+зач|warkocz"]),
    ("BIOZAVYVKA", [r"завивк|біозавивк|chemical.*curl"]),
    ("VYPRYAMLENNYA", [r"випрямлен|вирівнюван|випрямлення.*кератин|hair.*straight|brazilian|hair_botox|botox.*волос"]),
    ("POLIRUVANNYA_VOLOSSYA", [r"полірув.*волос"]),
    ("STRYZHKA_ZHINOCHA", [r"стрижк|haircut"]),

    # === NAROSHCHUVANNYA ===
    ("NAROSHCHUVANNYA_VOLOSSYA", [r"нарощ.*волос|hair.*extension|нарощ\b.*\b(?:капс|стрі|tape)"]),
    ("KOREKTSIYA_NAROSHCHENOGO_VOLOSSYA", [r"корекц.*нарощ.*волос"]),

    # === BROVI ===
    ("LAMINUVANNYA_BRIV", [r"ламінув.*брів|brow.*lamin"]),
    ("ARKHITEKTURA_BRIV", [r"архітект.*брів|архитек.*брів|архітект.*брови|brow.*architect"]),
    ("KOREKTSIYA_BRIV", [r"корекц.*брів|корекц.*брови|оформлен.*брів|оформлен.*брови|brow.*shap|воскова\s+корекц|brow\b|проріджуван.*брів|spa.*для\s*брів|спа.*для\s*брів|spa.*брів|спа\s+догляд.*брів"]),

    # === VII ===
    ("NAROSHCHUVANNYA_VII", [r"нарощ.*вій|lash.*extension|вії\s+\d|\dd\s+вії|пучок.*вій|пучки.*вій"]),
    ("LAMINUVANNYA_VII", [r"ламінув.*вій|botox.*вій|lash.*lift|lash.*lamin"]),
    ("KOREKTSIYA_VII", [r"корекц.*вій"]),

    # === MAKIYAZH ===
    ("MAKIYAZH_VESILNYI", [r"весіл.*макіяж|wedding.*makeup"]),
    ("MAKIYAZH_VECHIRNII", [r"вечір.*макіяж|smoky|smokey|evening.*makeup"]),
    ("MAKIYAZH_DENNYI", [r"денн.*макіяж|day.*makeup|нюдов"]),
    ("MAKIYAZH", [r"макіяж|маки[яе]ж|візаж|makeup|maquill"]),

    # === MANIKYUR ===
    ("MANIKYUR_YAPONSKYI", [r"японськ.*манікюр|p.?shine|японск.*маник|japanese\s+manicure"]),
    ("MANIKYUR_MEDYCHNYI", [r"медичн.*манікюр|медицинск.*маник|medical\s+manicure"]),
    ("MANIKYUR_APARATNYI", [r"апаратн.*манікюр|апаратн.*маник|e[\W_]file\s+manicure"]),
    ("MANIKYUR_KOMBINOVANYI", [r"комбінован.*манікюр|комбинирован.*маник"]),
    ("MANIKYUR_SPA", [r"spa.*манікюр|спа.*манікюр|spa.*manicure"]),
    ("MANIKYUR_CHOLOVICHYI", [r"чоловіч.*манікюр|мужск.*маник|male.*manicure"]),
    ("MANIKYUR_KLASYCHNYI", [r"класичн.*манікюр|класічн.*маникюр|класич.*маник|classic.*manicure"]),
    ("MANIKYUR", [r"манікюр|маникюр|manicure"]),

    # === POKRYTTYA / DESIGN NIGTIV ===
    ("POKRYTTYA_FRENCH_NAILS", [r"french.*ніг|french\b|френч"]),
    ("POKRYTTYA_GEL_LAK", [r"гель.?лак|shellac|шеллак|шелак|gel.?polish"]),
    ("POKRYTTYA_NIGTIV", [r"покрит.*нігт|nail.*polish|звичайн.*лак"]),
    ("DYZAIN_NIGTIV", [r"дизайн.*ніг|nail.*design|роспис|nail.*art|стрази|fol​га|фольга|втирка|блискіт|airbrush|аерограф"]),
    ("UKRIPLENNYA_NIGTIV_IBX", [r"ibx|укріплен.*ніг|акригел|укрепле.*ноготь|зміцнен.*ніг|гелем\s*/\s*полігелем|корекц.*натурал.*ніг|лікувальн.*лак|baehr\s+лікувальн|покрит.*лікувальн"]),
    ("REMONT_NIGTYA", [r"ремонт.*ніг|nail.*repair"]),
    ("POLIRUVANNYA_NIGTIV", [r"полірув.*ніг|nail.*polish.*buff"]),
    ("NAROSHCHUVANNYA_NIGTIV", [r"нарощ.*ніг|nail.*extension"]),
    ("KOREKTSIYA_NAROSHCHENYKH_NIGTIV", [r"корекц.*нарощ.*ніг"]),
    ("FORMA_NIGTIV", [r"форма.*ніг|nail.*shape"]),

    # === PEDIKYUR / PODOLOGIA ===
    ("PEDYKYUR_PODOLOGICHNYI", [r"подологіч.*педикюр|подологичн.*педикюр"]),
    ("ONIKHOMIKOZ", [r"оніхоміко|мікоз|fungal|onykhomik|онихомик"]),
    ("ONIKHOLIZYS", [r"оніхолі|онихоли|onykholiz"]),
    ("VROSLYI_NIGOT", [r"врослий\s+ніг|врослі\s+ніг|оніхокрипт|onykhokryp|онихокрипт|ingrow"]),
    ("HIPERKERATOZ", [r"гіперкератоз|гиперкератоз|hyperkerat"]),
    ("TRISCHCHYNY_PIAT", [r"тріщин.*п.ят|тріщин.*стоп|пяток|piat|heel.*crack"]),
    ("MAZOLI_NATOPTYSHI", [r"натоптиш|мозол|callus"]),
    ("KONSULTATSIYA_PODOLOGA", [r"консультац.*подолог|podolog.*consult"]),
    ("PEDYKYUR_YAPONSKYI", [r"японськ.*педикюр|p.?shine.*ноги|japanese\s+pedicure"]),
    ("PEDYKYUR_MEDYCHNYI", [r"медичн.*педикюр|медицинск.*педик|medical\s+pedicure"]),
    ("PEDYKYUR_APARATNYI", [r"апаратн.*педикюр|апаратн.*педик"]),
    ("PEDYKYUR_KOMBINOVANYI", [r"комбінован.*педикюр|комбинирован.*педик"]),
    ("PEDYKYUR_SPA", [r"spa.*педикюр|спа.*педикюр|spa.*pedicure"]),
    ("PEDYKYUR_CHOLOVICHYI", [r"чоловіч.*педикюр|мужск.*педик|male.*pedicure"]),
    ("PEDYKYUR_DYTYACHYI", [r"дитяч.*педикюр"]),
    ("PEDYKYUR_KLASYCHNYI", [r"класичн.*педикюр|classic.*pedicure"]),
    ("PEDYKYUR", [r"педикюр|педикур|pedicure|pediсure"]),
    ("SPA_NIH", [r"spa.*стоп|spa.*педи|podopharm.*стоп"]),

    # === FACE COSMETOLOGIA ===
    ("DOGLYAD_DMK", [r"\bdmk\b|енімова|ферментно|enzyme.*therapy|prozyme"]),
    ("DOGLYAD_CASMARA", [r"casmara"]),
    ("DOGLYAD_BIOLOGIQUE", [r"biologique"]),
    ("DOGLYAD_FORLLED", [r"forlled|forle|ForLLe"]),
    ("DOGLYAD_HYDROPEPTIDE", [r"hydropeptide"]),
    ("MIKROSTRUMOVA_TERAPIYA", [r"мікрострум|микростр|microcur"]),
    ("GIDROPILING_AQUAPURE", [r"aquapure|акваpure|гідропілінг"]),
    ("CHYSTKA_OBLYCHCHYA", [r"чистка.*облич|чищ.*облич|cleansing|face.*clean"]),
    ("CHYSTKA_SPYNY", [r"чистка.*спин|чищ.*спин"]),
    ("CHYSTKA_KOMBINOVANA", [r"комбінован.*чистк|комбинирован.*чистк"]),
    ("CHYSTKA_ATRAVMATYCHNA", [r"атравматич"]),
    ("PILING_OBLYCHCHYA", [r"піл.?інг|пиллинг|peeling"]),
    ("MASK_OBLYCHCHYA", [r"маска.*облич|маска.*шкір|маска\s+(?:альг|тканин|пігм)"]),
    ("DOGLYAD_OBLYCHCHYA", [r"догляд.*облич|догляд.*шкір|face.*care|skin.*care"]),

    # === ESTHETIC MEDICINE ===
    ("FILER_GUBY", [r"філер.*губ|губ.*філ|lip.*filler"]),
    ("FILER_NAVKOLO_OCHEI", [r"філер.*очей|області\s+навколо\s+очей|under.?eye"]),
    ("FILER_PIDBORIDDYA", [r"філер.*підборід|chin.*filler|джол|подбородок"]),
    ("FILER_KONTUR_OBLYCHCHYA", [r"філер.*вил|філер.*вилицев|cheek|skull|контур.*пластик|зон.*обличч.*філ|малярн"]),
    ("FILER_GIALURONIDAZA", [r"гіалуронідаз|гіалуронідазн|гіалуронидаз|гіалуроніда|hyaluronidase|зняття\s+філ|розчинит\s+філ"]),
    ("FILER_KONTUR_PLASTYKA", [r"контурн.*пластик|контурн.*філ|countur.*plastic"]),
    ("FILER", [r"філер|filler|juvederm|belotero|stylage|teosyal|radiesse|aliaxin|auralya|juvelook|aesthefill|estefil|rejuran\s*hb"]),

    ("BOTOX_LOB", [r"botox.*ло|ботокс.*ло|міжбрів|botox\s+forehead|forehead"]),
    ("BOTOX_OCHI", [r"botox.*оч|botox.*круг|ботокс.*оч|crow.*feet|gummy.*smile|ясенн.*посміш"]),
    ("BOTOX_PIDBORIDDYA", [r"botox.*підборід|ботокс.*підборід|chin.*botox|botox\s+chin"]),
    ("BOTOX_PLATYZMA", [r"platysma|платизм|шиї.*botox|nefretete|nefertete"]),
    ("BOTOX_BRUKSIZM", [r"бруксиз|bruksizm|bruxism"]),
    ("BOTOX_GIPERGIDROZ", [r"гіпергідроз|потовиді|hyperhid"]),
    ("BOTOX_NETRYMANNYA_SECHI", [r"нетриман.*сеч|incontinence"]),
    ("BOTOX_FULL_FACE", [r"full.*face|fullface"]),
    ("BOTOX", [r"botox|ботокс|botul|botul|alluzience|dysport"]),

    ("MEZOTERAPIYA_REJURAN", [r"rejuran"]),
    ("MEZOTERAPIYA_AKNE", [r"мезоте.*акне|акне.*мезо"]),
    ("MEZOTERAPIYA_VOLOSSYA", [r"мезо.*волос|hair.*mesoth|капіляр|capillary"]),
    ("MEZOTERAPIYA", [r"мезотерап|мезо\b|mesoth"]),
    ("BIOREVITALIZATSIYA", [r"біоревітал|биоревитал|biorevita|hyaluron.*injection"]),
    ("POLIMOLOCHNA_KYSLOTA", [r"полімолоч|polymolich|sculptra|поли\s*молочн"]),
    ("KOLAGEN_STIMULYATOR", [r"коллагенос|колагенос|colagenost|collagenost"]),
    ("NYTKY_LIFT", [r"нитков.*ліфт|nitk|нитки.*aptos|aptos|thread.*lift|титанов.*нитк|нитки\s+тит|polydioxan|pdo"]),
    ("KARBOKSITERAPIYA", [r"карбокси|carbox|карбоксі"]),
    ("ANESTEZIA", [r"анестезі|anesthes|inestes"]),

    # === BODY MASAZH ===
    ("MASAZH_ANTYTSELYULIT", [r"антицелюл|антициллюл|антитсилюл|antitselyul|anticellulit"]),
    ("MASAZH_LIMFODRENAZH", [r"лімфодрен|лимфодрен|lymph"]),
    ("MASAZH_SPORTYVNYI", [r"спортивн.*масаж|sport.*massag"]),
    ("MASAZH_OBLYCHCHYA", [r"масаж.*облич|face.*massag"]),
    ("MASAZH_SHYI", [r"шийно|шииа|шиї|комірц|шию|шейн.*комір"]),
    ("MASAZH_SPYNY", [r"масаж.*спин|back.*massag"]),
    ("MASAZH_NIH", [r"масаж.*ні|leg.*massag"]),
    ("MASAZH_RUK", [r"масаж.*рук|hand.*massag"]),
    ("MASAZH_GOLOVY", [r"масаж.*голов|scalp.*massag"]),
    ("MASAZH_KLASYCHNYI", [r"масаж|масс?аж|massag"]),

    # === BODY (other) ===
    ("OBGORTANNYA", [r"обгортан|wrap"]),
    ("SKRAB", [r"скраб|scrub"]),
    ("AIKUN_ICOONE", [r"icoone|айкун|айкунн|icooon"]),
    ("STRATOSFERA", [r"стратосфер"]),

    # === DEPILATION ===
    ("LAZER_EPILYATSIYA", [r"лазер.*епіляц|лазер.*эпиляц|laser.*hair.*remov|epil.*laser|лазерна.*епіляц"]),
    ("SHUGARING", [r"шугар"]),
    ("DEPIL_VOSKOM", [r"восков.*депіл|воском|wax.*depil|wax.*hair|бікіні\s+глибоке|глибоке\s+бікіні|депіляція\s+бікіні|депил.*бикини"]),
    ("EPILYATSIYA", [r"епіляц|эпиляц|depil|воск.*зона|епіляц.*ноги|epil"]),

    # === LASER (face) ===
    ("LAZER_OBLYCHCHYA", [r"лазер.*облич|лазерн.*тон|лазерн.*омолод|laser.*face|laser.*skin"]),
    ("LAZER_PIGMENTATSIYA", [r"лазер.*пігмент|pigment.*laser"]),
    ("LAZER", [r"лазер|laser"]),

    # === MISCELLANEOUS ===
    ("KOLORYSTYKA", [r"колорист|колорыст|color.*analy|sezonn.*kolory"]),
    ("KONSULTATSIYA", [r"консультац|consult"]),
    ("DIAGNOSTYKA", [r"діагност|diagnost"]),
    ("TEYPUVANNYA", [r"тейпув|teip|taping"]),
    ("AROMATERAPIYA", [r"аромат.*терапі|aromath"]),
]


def categorize_one(names: list[str], brands: list[str]) -> str:
    """Повертає category_id для канонічного ключа.

    Перебір по правилах: перший regex що матчиться у будь-якій з sample_names — виграє.
    Якщо нічого — OTHER.
    """
    haystack = " | ".join(names) + " | " + " | ".join(brands)
    haystack = haystack.lower()
    for cat, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, haystack, re.IGNORECASE):
                return cat
    return "OTHER"


def main() -> None:
    data = json.loads(INPUT.read_text(encoding="utf-8"))

    by_category: dict[str, list[dict]] = {}
    for entry in data:
        cat = categorize_one(entry["names"], entry["brands"])
        by_category.setdefault(cat, []).append(entry)

    # Summary
    summary = []
    for cat, items in sorted(by_category.items(), key=lambda x: -sum(i["svc_count"] for i in x[1])):
        total_svc = sum(i["svc_count"] for i in items)
        summary.append({"category": cat, "unique_keys": len(items), "svc_count": total_svc})

    OUTPUT.write_text(json.dumps({
        "summary": summary,
        "by_category": by_category,
    }, ensure_ascii=False, indent=2))

    total_keys = sum(s["unique_keys"] for s in summary)
    total_svc = sum(s["svc_count"] for s in summary)
    other = next((s for s in summary if s["category"] == "OTHER"), {"unique_keys": 0, "svc_count": 0})

    print(f"=== Категоризація ===")
    print(f"Total keys:        {total_keys}")
    print(f"Total services:    {total_svc}")
    print(f"Categories:        {len(summary)}")
    print(f"OTHER bucket:      {other['unique_keys']} keys ({other['svc_count']} services)")
    print(f"Coverage:          {(1 - other['unique_keys']/total_keys)*100:.1f}% keys / "
          f"{(1 - other['svc_count']/total_svc)*100:.1f}% services\n")

    print(f"=== Топ-30 категорій ===")
    for s in summary[:30]:
        print(f"  {s['category']:35s} {s['unique_keys']:5d} keys  {s['svc_count']:5d} services")

    if other["unique_keys"]:
        print(f"\n=== Перші 30 OTHER (потребують правил) ===")
        for entry in by_category.get("OTHER", [])[:30]:
            names_preview = " / ".join(entry["names"][:2])
            print(f"  {entry['svc_count']:4d}× {entry['canonical_key']:50s} ← {names_preview[:80]}")


if __name__ == "__main__":
    main()
