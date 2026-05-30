"""Universal extension — класифікує ВСІ uncovered canonical_keys → existing profiles.

Дозволяє розширити family-options існуючих profiles реальними ключами що зараз
проходять повз (Hair брендові процедури, Nails укріплення варіанти, Pedicure SPA-маски,
Cosmetology брендові процедури, тощо).

Запуск:
    python -m scripts.extend_profile_keys_universal              # dry
    python -m scripts.extend_profile_keys_universal --apply
"""
from __future__ import annotations

import argparse
import asyncio
import re

from sqlalchemy import select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption,
)
from app.infrastructure.db.session import build_engine, build_session_factory


# Regex patterns → target profile name (per country='ua').
# Specific перед загальним. Patterns матчать на 'canonical_key | service_name'.
MAPPING: list[tuple[str, str]] = [
    # ─── HAIR ─────────────────────────────────────────────────────
    # Освітлюючі креми/масла → Мелірування
    (r"osvitlyuyuchyi|osvitlyayuchyi|osvitlennya|masla_osvitlyuyuchi|krem_osvitl|biostetique_krem|biostetique_osvitl|art_krem_osvitl|lyabiostatik|lights_meliruvannya|meliru|melyruvann|airtouch|shatush|balayazh|babylights|vual|kontur|venetsian|kaliforn|sun_lights|diagonal", "Мелірування"),
    # Тонування techniques/varianty
    (r"shine_tone|tonuvannya_shine|tonuvannya_alfaparf|goldwell_tonuvannya|alfaparf_tonuvannya|tonuvannya_yellow|tonuvannya_goldwell|color_gloss|vibrance|^tonuvannya", "Тонування"),
    # Фарбування варіанти (spetsblond, single tone, корені)
    (r"spetsblond|farbuvannya_spetsblond|farbuvannya_korinnya|farbuvannya_koreniv|kolorov|odnotonne|farbuvannya_tint_tone|farbuvannya_keune|farbuvannya_pry|farbuvannya_ukladka|farbuvannya_keratin|color_farbuvannya_gloss", "Фарбування коренів"),
    # Зняття кольору / змивка
    (r"znyattya|zmyvka|vykhid_z_chornogo|chornogo_vykhid|chornogo_farbuvannya_iz|tempting|organichna_zmyvka", "Зняття кольору"),
    # Передпігментація
    (r"peredpigment|peredpigmentuvannya|peredpigmentatsiya", "Передпігментація"),
    # AFRO
    (r"afronakrutka|afrokudri", "Афрокудрі"),
    # Плетіння
    (r"pletinnya|kosychok|dekor_pir|pir_v_volossya|piryachko", "Плетіння"),
    # Зачіска вечірня
    (r"vesilna|svyatkova|urochysta|zachiska|gollivud|khvylya|khvyli|^khvist | khvist | khvist$|khvistom", "Зачіска вечірня"),
    # Брендові маски / процедури для волосся → Глибокий курс відновлення (для лікування)
    # та Догляд преміум-брендами (для брендових процедур з ритуалом)
    (r"sorbie|maska_spa|maska_smooth|ekspres_maska|fluid_vita|anti_freez|vita_fluid", "Глибокий курс відновлення"),
    (r"hadat|formula_glybokogo|secret_formula|secretna_formula|kryzhane_syaivo|kholodne|luxury_syaivo|ice_kholodne|sekretna|rozumnyi_detoks", "Глибокий курс відновлення"),
    (r"orising|antybakterialna_lupy|sukhoi_lupy|farbuvanni_orising_pry_zakhyst", "Догляд преміум-брендами"),
    (r"napla|imprime|repair_spa|premier_repair", "Догляд преміум-брендами"),
    (r"amethyste|colouring_mask|farbuyucha_mask", "Догляд преміум-брендами"),
    (r"link_d|d_link|zmitsnennya_link", "Глибокий курс відновлення"),
    (r"suda|fedua|likuvalne_pokryttya", "Догляд преміум-брендами"),
    (r"detoks_dlya_golovy|piling_shkiry|golovy_label_piling|label_piling|golovi_pilingom|zhyvlennya_shkiry", "Глибокий курс відновлення"),
    (r"^one$|lebel one|lebel_one|brae_ekspres|brae_revival|brae_bond|bond_angel|bondangel|brae_power|brae_povnyi_rytual|farbuvanni_pry_zakhyst|zakhyst_brae|volossya_zakhyst|pcc_zakhyst|tokio_inkarami|inkarami|cronna|milbon|protsedura_vidnovlennya|spa_doglyad|doglyad_milbon|doglyad_brae|rekonstruktsiya_revival|povnyi_rytual|povnyi_revival_rytual|dnya_natkhnennya|oribe_rytual|masla_osvitlyuyuchi|brae_ampul|lanza|lanza_likuvannya|osvitlenni_pry_zakhyst|zakhyst_pry_osvitlenni", "Догляд преміум-брендами"),
    (r"awg_likuvannya|ekspres_likuvannya|ampul|shchaslyve_ukladka|shchastya|absolyutne_dlya|absolyutne_likuvannya|likuvannya_volossya_lebel|kera_vita|kera_koktei|sirovatka|sirovatka_do", "Експрес-догляд ампулами"),
    (r"my_force_vidnovlennya|vidnovlennya_my_force|vidnovlennya$|vidnovlennya_pry|vidnovlennya_dr|pid_vologa_zamkom|vologa_zamkom|dlya_maska_volossya|maska_volossya|maslom_obgortannya|krem_skrab_spa$|ochyshchennya_shkiry|volossya_zmitsnennya|patchi_vid|biozavyvka|zavyvka", "Глибокий курс відновлення"),
    # Філлер для волосся Nashi
    (r"filler_servise|filler_option|nashi_filler", "Догляд преміум-брендами"),
    # БТХ Innovatis / випрямлення
    (r"btkh_hair_innovatis|btx_innovatis|innovatis|dzerkalne_vyrivnyuvannya|brazilian_blowout|blowout_brazilian|vypryamlennya", "Кератинове випрямлення / Ботокс для волосся"),
    # Укладка LUX
    (r"lyuks_ukladka|kosmetytsi_lyuks_na_ukladka|kosmetytsi_lyuks_na_ukladannya|garyachyi_instrument_kosmetytsi_lyuks|oribe_ukladka|lyuks_oribe|balmain|kosmetytsi_lyuks_myttya", "Укладка LUX"),
    # Укладка нарощеного
    (r"naroshche|naroshchene_ukladka|naroshchenogo_ukladka|lyuks_naroshchene|lyuks_naroshchenogo", "Укладка нарощеного волосся"),
    # Укладка базова
    (r"lokony_ukladka|garyachyi_instrument_na_ukladka|ukladannya|ukladka|^hair_long$|long_hair", "Укладка"),
    # Нарощування волосся (50g, 100g тощо)
    (r"100g_naroshchuvannya|150g_naroshchuvannya|50g_naroshchuvannya|100_g_naroshchuvannya|150_g_naroshchuvannya|50_g_naroshchuvannya|100_gramm_naroshchuvannya|150_gramm_naroshchuvannya|50_gramm_naroshchuvannya|kapsulamy_mikro_naroshchuvannya|tape_naroshchuvannya|kapsulne_naroshchuvannya|nano_kapsulne_naroshchuvannya|nano_naroshchuvannya|bez_naroshchenogo_naroshchuvannya|obslugovuvannya_systemy_volossya|znyattya_naroshchenogo_volossya|znyattya_volossya_naroshchenogo|naroshchuvannya_volossya|naroshchuvannya_volossia", "Нарощування волосся"),
    # Стрижка чубчика
    (r"chubchyk|chubchyka", "Стрижка чубчика"),
    # Дитяча стрижка
    (r"dytyacha|dytyna|dityachoi|dityacha|dityachi", "Дитяча стрижка"),
    # Стрижка жіноча
    (r"stryzhka_zhinocha|stryzhka_dovgogo|stryzhka_korotkogo|^stryzhka_|stryzhka$|polirovannya|poliruvannya", "Стрижка жіноча"),
    # Блонд миття
    (r"blond_myttya|super_blond_myttya", "Блонд миття"),

    # ─── NAILS ────────────────────────────────────────────────────
    # Дизайн нігтів (1 ніготь — окрема послуга художня)
    (r"1_dyzain_nigot|dyzain_nigot|dyzain_1_nigot|design_nail|dyzain_naroshchenykh_nigtiv|gradiyent|stemping|nalipky|dyzain_gradiyent|dyzain_2|dyzain_1_riven|dyzain_2_riven|dyzain_3_riven|strazy|nigot_strazy|nigot_vtyrka|vtyrka|chunky_holo|dyzain_chunky|amalgam|kamifubuki|broshka|10_dyzain_kotyache_nigtiv_oko|kotyache_oko_dyzain|dyzain_kotyache|dyzain_kotyache_oko|dyzain_kosmichne|dyzain_metalick|metalik_dyzain", "Дизайн нігтів"),
    # Френч / гель-лак
    (r"frantsuzkyi_gel_lak|frantsuzkyi_manikyur_gel|gel_lak_french|french_gel_lak|lak_nail_tech_vinilux|vinilux|nail_tech|gel_koshache_lak_manikyur_oko|gel_koshache_lak_oko|koshache_oko|gel_lak_koshache|gel_lak_oko", "Манікюр + покриття гель-лак"),
    (r"gel_koshache_lak_oko_pedykyur|gel_koshache_lak_pedykyur|gel_lak_pedykyur_koshache", "Педикюр + покриття гель-лак"),
    # Чоловічий манікюр (для чоловіків)
    (r"cholovikam_manikyur|cholovichyi_manikyur_dlya|manikyur_dlya_cholovikiv|cholovikiv_dlya_manikyur|cholovichyi_manikyur_massazh_spa|cholovichyi_manikyur_masazh|cholovichyi_yaponskyi_manikyur|cholovichyi_japanese_manikyur", "Чоловічий манікюр"),
    # Чоловічий педикюр (для чоловіків)
    (r"cholovichyi_masazh_pedykyur_spa|cholovichyi_pedykyur_dlya|pedykyur_dlya_cholovikiv|cholovikiv_dlya_pedykyur|cholovichyi_pedykyur_massazh", "Чоловічий педикюр"),
    # Форма нігтів — підпил, форма без покриття
    (r"bez_formy_manikyuru_pidpyl|pidpil|forma_manikyuru|forma_nigtiv|forma_nigot|nail_shape", "Форма нігтів"),
    # Укріплення IBX (existing profile)
    (r"\bibx\b|ukriplennya_ibx|ibx_ukriplennya", "Укріплення IBX"),
    # Укріплення варіанти гелем / полігелем
    (r"gelem_poligelem_ukriplennya|polygel_ukriplennya|akrygel_ukriplennya|akrigel|polygel|polige_lukriplennya|bottle_gel_ukriplennya|bottle_gel|ukriplennya_bottle|ukriplennya_polig|ukriplennya_akrigel|1_ukriplennya|3_ukriplennya|5_ukriplennya|7_ukriplennya|akrylovoyu_pudroyu_ukriplennya|akrylovoyu_pudroyu_zmitsnennya|akryl_pudr|zmitsnennya|ukriplennya_volossya|gelem_nigtiv_ukriplennya|10_4_gelem_nigtiv_ukriplennya|10_gelem_ukriplennya|gelem_ukriplennya_10|ukriplennya_gelem", "Укріплення гелем / полігелем / акригелем"),
    # Нарощування нігтів варіанти
    (r"naroshchuvannya_nigtya_odnogo|odnogo_nigtya_naroshchuvannya|1_kapsuly_naroshchuvannya|kapsulyamy_naroshchuvannya|naroshchuvannya_kapsulyamy|naroshchuvannya_naroshchenykh|naroshchuvannya_nigtiv|luxio_naroshchuvannya|1_naroshchuvannya_nigtiv|3_naroshchuvannya_nigtiv|5_naroshchuvannya_nigtiv|7_naroshchuvannya_nigtiv|1_luxio_naroshchuvannya|3_luxio_naroshchuvannya|5_luxio_naroshchuvannya|7_luxio_naroshchuvannya|1_naturalnogo_nigtya_remont_sht|remont_nigtya|remont_natural_nigtya|arkhitektury_nigtiv_vidnovlennya|arkhutektury_nigtiv_vidnovlennya|nigtiv_arkhitektury|vidnovlennya_arkhitektury_nigtiv", "Нарощування нігтів"),
    # SPA для рук + skraby / masky → SPA-догляд для рук
    (r"dlya_podopharm_ruk|dlya_podofarm_ruk|dlya_padopharm_ruk|dlya_ruk_podopharm|dlya_ruk_podofarm|dlya_ruk_padopharm|dlya_maska_ruk_skrab_spa|dlya_maska_podopharm_ruk|dlya_maska_podofarm_ruk|dlya_maska_padopharm_ruk|skrab_dlya_ruk_spa|spa_skrab_dlya_ruk|dlya_ruk_maska_spa|ruk_dlya_spa_skrab|povnistyu_ruky|ruky_povnistyu|baehr_spa_svichka|spa_svichka_baehr|svichka_spa|manikyur_spa|spa_manikyur|ruky_spa_ukhod|spa_ukhod_ruky", "SPA-догляд для рук"),

    # ─── PEDICURE ────────────────────────────────────────────────
    # Скраби/маски для ніг → SPA для стоп
    (r"dlya_nig_podopharm_skrab|dlya_nig_podofarm_skrab|dlya_nig_padopharm_skrab|dlya_podopharm_nig|dlya_podofarm_nig|dlya_padopharm_nig|dlya_maska_nig_podopharm|dlya_maska_nig_podofarm|dlya_maska_nig_padopharm|dlya_maska_nig_skrab_spa|spa_dlya_nig_skrab|nig_dlya_spa_skrab|skrab_dlya_nig|paltsi_pedykyur_stopa|stopa_pedykyur_paltsi|nogy_povnistyu|povnistyu_nogy|pedykyur_spa|spa_pedykyur|nogy_spa_ukhod|spa_ukhod_nogy", "SPA-догляд для стоп"),
    # Тон ніг
    (r"nig_ton|ton_dlya_nig|ton_nig", "SPA-догляд для стоп"),
    # Медичний педикюр → Подологічний педикюр
    (r"medychnyi_pedykyur|medychnyi_pedykyur_top|medychnyi_zhinochyi_pedykyur|3_abo_diabetychna_glybynni_medychnyi_mikozu|3_glybynni_medychnyi_mikozu_nigtovoi_obrobka_onikhogrifozu_onikholizisu_pedykyur_pla|stadiya_medychnyi_pedykyur", "Подологічний педикюр"),
    # Масаж ніг після педикюру → SPA-догляд для стоп
    (r"masazh_nig_pedykyuru|masazh_nig_pislya_pedykyuru|masazh_stop_pedykyuru", "SPA-догляд для стоп"),
    # Частковий педикюр стопи
    (r"chastkovyi_pedykyur_stopy|chastkovyi_pedykyur_zhinochyi|bez_formy_pedykyuru_pidpyl|chastkovyi_pedykyur_paltsi|chastkovyi_paltsi_pedykyur|paltsi_pedykyur_chastkovyi|chastkovyi_paltsiv_pedykyur|chastkovyi_pedykyur_s|chastkovyi_pedykyur", "Частковий педикюр"),
    # Подологічний частковий
    (r"chastkovyi_paltsi_pedykyur_podologichnyi|chastkovyi_pedykyur_podologichnyi_stopa|paltsi_pedykyur_podologichnyi|podologichnyi_chastkovyi_pedykyur", "Подологічний педикюр"),
    # Оніхолізис (вже існує profile Обробка оніхолізису)
    (r"obrobka_onikholizysu|obrobka_onikholizisa|onikholizys|onikholizis|onikholizys_zachystka|zachystka_onikholizysu|1_3_nigot_obrobka_onikholizysu|1_2_nigot_obrobka_onikholizysu|1_1_nigot_obrobka_onikholizysu", "Обробка оніхолізису"),
    # Оніхомікоз / Оніхогрифоз
    (r"obrobka_onikhogryfozu|obrobka_onikhogrifozu|onikhogryfoz|onikhogrifoz|3_mikozom_nigtiv_obrobka|mikozom_nigtiv|5_obrobka_od_onikhogryfozu|obrobka_nigtiv_mikoz|mikoz_obrobka_nigtiv", "Обробка оніхомікозу / оніхогрифозу"),
    # Врослий ніготь (онихокриптоз)
    (r"2_granulomoyu_nabryakom_nigtya_protsesom_rezektsiya|rezektsiya_vroslogo_nigtya|vroslogo_nigtya_rezektsiya|vroslogo_nigtya|onikhokryptoz|onyhokryptoz|nigtya_vroslyi", "Врослий ніготь (онихокриптоз)"),
    # Подологічний педикюр чоловічий медичний
    (r"cholovichyi_giperkeratoz_medychnyi_mozoliv|cholovichyi_medychnyi_pedykyur_stadiya|medychnyi_cholovichyi_pedykyur|1_cholovichyi_giperkeratoz_medychnyi_mozoliv", "Подологічний педикюр"),
    # Ортезі (Виготовлення)
    (r"1_deformatsii_indyvidualnykh_likuvannya_orteziv|orteziv_vygotovlennya|orteziv_indyvidualnykh|vygotovlennya_orteziv|ortezy_vygotovlennya", "Подологічний педикюр"),
    # Депіляція обличчя
    (r"areoliv_depilyatsiya_guby_paltsiv|verkhnoi_guby_depilyatsiya|pidboriddya_depilyatsiya|paltsiv_depilyatsiya|areoliv_depilyatsiya", "Депіляція обличчя"),
    # Діабетична стопа
    (r"diabetychnoi_obrobka_stopy|diabetychna_stopa|diabetychnyi", "Подологічний педикюр"),

    # ─── BROWS / LASHES ──────────────────────────────────────────
    (r"briv_kompleks_laminuvannya|kompleks_laminuvannya_briv|kompleksne_laminuvannya_briv|briv_laminuvannya_kompleks", "Ламінування брів"),
    (r"proridzhuvannya_briv|briv_proridzhuvannya|briv_korrektsiya_skladna|briv_korektsiya_skladna|skladna_korektsiya_briv|briv_modelyuvannya_voskom|modelyuvannya_briv_voskom|briv_korektsiya_voskom|briv_voskom|briv_arkhitektura|arkhitektura_briv", "Корекція + фарбування брів"),
    # Permanent makeup brows
    (r"briv_makiyazh_permanentnyi|permanentnyi_makiyazh_briv|permanent_briv|tatuazh_briv|briv_tatuazh|microblading|mikroblading", "Корекція + фарбування брів"),
    (r"laminuvannya_vidnovlennya_vii|vii_laminuvannya_vidnovlennya|laminuvannya_vii_vidnovlennya|vidnovlennya_vii|botoks_dlya_vii|vii_botoks|laminuvannya_botoks_vii|botoks_laminuvannya_vii", "Ламінування вій"),
    (r"strichky_vii|vii_strichky|strichkoyu_vii|2d_korektsiya_vii|3d_korektsiya_vii|4d_korektsiya_vii|korektsiya_2d_vii|korektsiya_3d_vii|korektsiya_4d_vii|1_puchok_sht|puchok_sht|puchky_vii|naroshchuvannya_vii_korektsiya|korektsiya_vii_2d|korektsiya_vii_3d|korektsiya_vii_4d|vii_2d|vii_3d|vii_4d|keroplastyka_vii|vii_keroplastyka|klasychne_naroshchuvannya_vii|klasyka_korektsiya_vii|klasyka_vii|hollywood_vii|2d_naroshchuvannya_vii|3d_naroshchuvannya_vii|4d_naroshchuvannya_vii", "Нарощування вій"),

    # ─── PODOLOGY ────────────────────────────────────────────────
    (r"mozolyu_obrobka_stryzhnevogo|obrobka_stryzhnevogo|mozoli_pidnigtovoi|mazoli_pidnigtovoi|pidnigtovoi_mozolyu|pidnigtovoi_mazoli|nigot_mozol|mozol_obrobka|natoptyshiv|borodavky_kozhnoi_nastupnoi|borodavky_nastupnoi|borodavky_zachystka|borodavka_arr|kozhnoi_borodavky|1_borodavky_od_vydalennya|borodavky_od_vydalennya|vydalennya_borodavky|borodavky_vydalennya|borodavka|borodavok|borodavok_mnozhynni|mnozhynni_borodavok|^mozol|mozolya|likuvannya_obrobka_serednikh_stopy_ta_trishchyn|likuvannya_obrobka_glybokykh|likuvannya_obrobka_poverkhnevykh|trishchina_na_pyattsi|na_pyattsi_trishchina|na_pyatkakh_trishchina|trishchin|vroslyi_nigot|nigot_vroslyi|onikhomikoz|obrobka_onikhomikozu|obrobka_onikhomikoznoi", "Гіперкератоз / Натоптиші / Мозолі"),

    # ─── HAIR proridzhuvannya — стрижка ───────────────────────────
    (r"\bproridzhuvannya\b|stryzhka_proridzh|proridzhuvannya_volossya", "Стрижка жіноча"),

    # ─── MASSAGE ─────────────────────────────────────────────────
    (r"likuvalnyi_masazh|likuvalnyi_dip|dip_likuvalnyi|dip_masazh|ozdorovchyi_zagalno|zagalno_ozdorovchyi|zagalnoozdorovchyi|likuvalno_profilaktychnyi", "Спортивний масаж"),

    # ─── PILING / CHISTKA + декольте ──────────────────────────────
    (r"piling_prx|prx_t33|piling_simildiet|simildiet|bio_re_peel|bio_peel|peptiglow|retix_c|piling_retix|dekolte_oblychchya_piling|piling_dekolte|dekolte_piling|piling.*dekolte", "Пілінг"),
    (r"dekolte_ekzosomy|ekzosomy|ekzo_terapiya|ekzosomy_oblychchya|maska_dermo28|dermo28|dermo_28|vital_mask|maska_holyland|holyland_doglyad", "Чистка обличчя"),
    (r"karboksiterapiya_ribeskin|karboksiterapiya_dekolte|karboksi_dekolte|karboksiterapiya_oblychchya|ribeskin_karboksi", "Карбоксітерапія"),
    (r"dekolte_makiyazh_oblychchya_vesilnyi|vesilnyi_makiyazh|vesilnyi_makiazh|makiyazh_vesilnyi|makiyazh_dekolte_vesilnyi|makiyazh_anti_age", "Макіяж для особливих випадків"),

    # ─── DEPILYATSIYA / EPIL ─────────────────────────────────────
    (r"depilyatsiya_linii_zhyvota|depilyatsiya_zhyvit|liniya_depilyatsiya_zhyvota|depilyatsiya_sidnyts|sidnyts_depilyatsiya|depilyatsiya_stegon|stegon_depilyatsiya|depilyatsiya_grud|depilyatsiya_zhyvota|depilyatsiya_spyny|depilyatsiya_ruk|depilyatsiya_nig|depilyatsiya_bikini|depilyatsiya_pakhva|depilyatsiya_pakhv|depilyatsiya_dekolte", "Воскова депіляція"),
    (r"depilyatsiya_oblychchya|oblychchya_depilyatsiya|depilyatsiya_guba|guba_depilyatsiya|depilyatsiya_pidboriddya|depilyatsiya_dekolte_guba|verkhnya_guba", "Депіляція обличчя"),

    # ─── DEMAKIYAZH ──────────────────────────────────────────────
    (r"demakiyazh|demakiazh|znyattya_makiyazhu|znyattya_makiazhu|znyattya_makijazhu|premakeup", "Догляд перед макіяжем"),

    # ─── CONSULTATION ────────────────────────────────────────────
    (r"dermatologa_konsultatsiya|konsultatsiya_dermatologa|laboratoriy|laboratornykh_doslidzhen|zabir_materialu", "Загальна консультація"),

    # ─── SPA-PROCEDURES для рук / ніг ────────────────────────────
    (r"dlya_gidrogeleva_maska_ruk|gidrogeleva_maska_ruk|maska_gidrogeleva_ruk|dlya_parafin_protsedura_ruk_spa|parafin_ruk_spa|parafin_dlya_ruk|do_liktya_masazh_ruk_spa|masazh_ruk_spa|spa_masazh_ruk|dlya_dlya_maska_ruk_spa|dlya_spa_ruk|spa_dlya_ruk|spa_protsedura_ruk", "SPA-догляд для рук"),
    (r"dlya_gidrogeleva_maska_nig|gidrogeleva_maska_nig|maska_gidrogeleva_nig|dlya_nig_protsedura_spa|dlya_nig_ruk_skrab_spa|nig_skrab_spa|skrab_dlya_nig_spa|spa_dlya_nig|spa_protsedura_nig|do_kolina_masazh_stop|masazh_stop_spa|spa_masazh_stop", "SPA-догляд для стоп"),

    # ─── DYTYACHE покриття ───────────────────────────────────────
    (r"dytyache_lakom_pokryttya|dytyache_pokryttya|dytyacha_manikyur|dytyachyi_manikyur|dytyacha_manikyur_dlya|dytyacha_pokryttya_lakom", "Покриття гель-лак"),

    # ─── PERMANENT MAKEUP (нові brow / lips перманент) ───────────
    (r"gub_makiyazh_permanentnyi|gub_makiazh_permanent|permanentnyi_makiyazh_gub|gub_permanent|gub_napylennya_pudrove|gub_korektsiya_makiyazhu_permanentnogo|akvarelnyi_gub|tatuazh_gub|gub_tatuazh", "Корекція + фарбування брів"),

    # ─── REJURAN ───────────────────────────────────────────────────
    (r"rejuran|rejuran_hb|rejuran_healer|hb_liftyng_plus_rejuran|healer_oblychchya_rejuran|rejuran_s|rejuran_oblychchya|rejuran_shyi", "Rejuran"),

    # ─── HOLY LAND / piling chimichnyi ──────────────────────────────
    (r"holy_land|holyland|khimichnyi_piling|piling_khimichnyi|piling_holyland|piling_glow|glow_jalupro|jalupro_glow|holy_khimichnyi|khimichnyi_land|piling_poverkhnevyi|poverkhnevyi_piling|piling_glybokyi|glybokyi_piling|piling_seredniy|piling_alfa|^piling | piling$|piling_alginat|piling_lyma|piling_yamchasta|piling_obersopher|piling_smart|piling_neostrata|piling_gly|piling_mandelic|piling_jessner|piling_resorcin|piling_lactic|piling_tca|tca_piling|gly_piling", "Пілінг"),

    # ─── HIALURONIDAZA ──────────────────────────────────────────────
    (r"gialuronidaza|hyaluronidaza|hialuronidaza", "Філлери (контурна пластика)"),

    # ─── GRYBOK / ONIKHOMIKOZ ──────────────────────────────────────
    (r"grybka_obrobka|grybkovoi_obrobka_stopy|obrobka_grybka|stopy_grybka|grybok_stopy", "Обробка оніхомікозу / оніхогрифозу"),

    # ─── TRISHCHYNY (deep) ──────────────────────────────────────────
    (r"dopomogoyu_glybokykh_kysloty_likuvannya_obrobka_skalpelya_stopy_ta_ta_trishchyn_za|skalpelya_obrobka|glybokykh_trishchyn|likuvannya_obrobka_glybokykh", "Тріщини на стопах"),

    # ─── BODY SCULPT / FIGURE ──────────────────────────────────────
    (r"figury_korektsiya|korektsiya_figury|figura_korektsiya|figury_modelyuvannya|sculpting", "Endospheres / Endosphere"),

    # ─── KOROLIVSKE GOLINNYA ───────────────────────────────────────
    (r"golinnya_korolivske|korolivske_golinnya|royal_shave|golinnya_brytv", "Борода та вуса"),

    # ─── FORMA OPYL ────────────────────────────────────────────────
    (r"forma_opyl|opyl_forma|opylennya|opyl_nigtya", "Форма нігтів"),

    # ─── GEL-LAK додаткові ─────────────────────────────────────────
    (r"gel_lak_luxio_oniq|gel_lak_luxio|gel_lak_oniq|oniq_gel_lak|gel_lak_na_pedykyuri|gel_lak_pedykyur_pokryttya|pokryttya_gel_lak_pedykyur", "Покриття гель-лак"),

    # ─── GIPERKERATOZ обробка (variant) ────────────────────────────
    (r"giperkeratoza_obrobka|obrobka_giperkeratoza|giperkeratoza|giperkeratozu_obrobka_stopy|obrobka_giperkeratozu", "Гіперкератоз / Натоптиші / Мозолі"),

    # ─── FULL FACE pyling/laser ────────────────────────────────────
    (r"face_full_legkyi|full_face_legkyi|full_face|face_full_depilyatsiya|full_face_depilyats", "Лазерна епіляція"),

    # ─── GUB / PIDBORIDDYA / VERKHNYA depilatsiya ──────────────────
    (r"dekolte_guba_pidboriddya_verkhnya|verkhnya_dekolte_guba_pidboriddya|guba_verkhnya_pidboriddya|verkhnya_guba_pidboriddya|guba_pidboriddya_dekolte", "Депіляція обличчя"),

    # ─── MASAZH dekolte+oblychchya+shyya ───────────────────────────
    (r"dekolte_masazh_oblychchya_shyya|masazh_oblychchya_dekolte|masazh_dekolte_oblychchya|masazh_dekolte_shyya|masazh_dekolte", "Локальний масаж (голова, шия, спина, обличчя)"),

    # ─── GELEM UKRIPLENNYA додатково ───────────────────────────────
    (r"gelem_ukriplennya|ukriplennya_gelem|ukriplennya_polig|ukriplennya_akrigel|polig_ukriplennya|akrigel_ukriplennya|gel_ukriplennya|gel_zmitsnennya|polig_zmitsnennya", "Укріплення гелем / полігелем / акригелем"),

    # ─── EPIDERM Ribeskin mask → cosmetology mask ──────────────────
    (r"epiderm_mask_maska_plus_ribeskin|ribeskin_epiderm|epiderm_ribeskin", "Альгінатна маска"),

    # ─── KUMA SHAPE generic ────────────────────────────────────────
    (r"kuma|shape_lokalni|shape_nizhky|shape_poperek|shape_ruky|shape_spyna|shape_zhyvit|shape_yagodytsi|shape_sidnytsi|shape_pidboriddya|shape_pidshyiya|shape_ruka|lpg_masazh|lpg|^lpg", "Endospheres / Endosphere"),

    # ─── KUTYKULY / Японський manicure / pedicure ─────────────────
    (r"kutykuly_manikyur_obrobkoyu_yaponskyi|kutykuly_yaponskyi_manikyur|yaponskyi_manikyur_kutykuly|yaponskyi_manikyur", "Манікюр японський"),
    (r"kutykuly_obrobkoyu_pedykyur_yaponskyi|kutykuly_yaponskyi_pedykyur|yaponskyi_pedykyur_kutykuly|yaponskyi_pedykyur", "Педикюр японський"),

    # ─── KOMPLEKS МАНІКЮР + НАРОЩУВАННЯ ───────────────────────────
    (r"kompleks_manikyur_naroshchuvannya|naroshchuvannya_kompleks_manikyur|manikyur_naroshchuvannya|nigtya_naroshchuvannya|kvadratu_restavratsiya|restavratsiya_kvadratu|krayu_vilnogo_restavratsiya|restavratsiya_nigtya|krayu_pidnyattya_vilnogo|krayu_ukriplennya_vilnogo|pidnyattya_krayu", "Нарощування нігтів"),

    # ─── КАМУФЛЯЖ ─────────────────────────────────────────────────
    (r"komuflyuvannya|kamuflyuvannya|kamuflyaz", "Чоловіче фарбування / Камуфляж"),

    # ─── МАСКА ШВИДКОЇ КРАСИ / WOW MASK ───────────────────────────
    (r"hyalual_mask_maska_wow|hyalual|wow_mask|krasy_maska_myttyevoi|myttyevoi_krasy|maska_krasy", "Альгінатна маска"),

    # ─── PERMANENT BROWS типи / KORONA брови ──────────────────────
    (r"korona_briv|trihoblending|akvarel_briv", "Корекція + фарбування брів"),

    # ─── LAZER (рідкий лазер) ─────────────────────────────────────
    (r"lazer_ridkyi|ridkyi_lazer|liquid_laser|electroporation_lazer", "Електропорація / Мікрострумова терапія"),

    # ─── ZONA NIG ZA KOLIN ─────────────────────────────────────────
    (r"kolina_nogy_nyzhche_vyshche|nogy_nyzhche_kolin|nogy_vyshche_kolin|vyshche_kolin_nogy|nyzhche_kolin_nogy", "Лазерна епіляція"),

    # ─── KARBOXITERAPIYA TILA ─────────────────────────────────────
    (r"karboksiterapiya_tila|karboksy_tila|karboksi_tila|tila_karboksi", "Карбоксітерапія"),

    # ─── INDYVIDUALNOGO ROZVANTAZHENNYA ──────────────────────────
    (r"indyvidualnogo_rozvantazhennya|rozvantazhennya_indyvidualnogo|individualne_rozvantazhennya", "Подологічний педикюр"),

    # ─── SPA SVIZHA KHODA ─────────────────────────────────────────
    (r"khoda_posluga_spa_svizha|svizha_khoda|svizha_khoda_spa|spa_khoda", "SPA-догляд для стоп"),

    # ─── LAIT MAKIYAZH ────────────────────────────────────────────
    (r"lait_light_makiyazh|light_makiyazh|lait_makiyazh|makiyazh_light", "Макіяж денний"),

    # ─── LAK ZVYCHAINYI ───────────────────────────────────────────
    (r"lak_zvychainyi|zvychainyi_lak|simple_lak", "Покриття гель-лак"),

    # ─── LIMFODRENAZH OBLYCHCHYA ─────────────────────────────────
    (r"limfodrenazhnyi_masazh_oblychchya|masazh_oblychchya_limfodrenazh|limfodrenazh_oblychchya", "Лімфодренажний масаж"),

    # ─── PEREVYAZKA likuvalnymy zasobamy ─────────────────────────
    (r"likuvalnymy_perevyazka|perevyazka_likuvalna|likuvalna_perevyazka", "Тріщини на стопах"),

    # ─── DERMAHEAL / MEZO  ───────────────────────────────────────
    (r"dermaheal|ll_obyemu_pidboriddya|obyemu_pidboriddya|pidboriddya_zmenshennya|zmenshennya_obyemu|jalupro_hmw|jalupro_super|jalupro_3ml|nctf|saypha|teosyal|skinbooster|skin_booster|profhilo|hmw|polipeptyd|polynukleotyd", "Мезотерапія"),

    # ─── LUNKY (дизайн) ──────────────────────────────────────────
    (r"^lunky$|^lunky | lunky$|lunky_dyzain|french_lunky", "Дизайн нігтів"),

    # ─── ВИПУСКНИЙ МАКІЯЖ ───────────────────────────────────────
    (r"makiyazh_vypusknyi|vypusknyi_makiyazh|makiyazh_dlya_vypuska|prom_makiyazh", "Макіяж для особливих випадків"),

    # ─── МЕДИЧНИЙ МАНІКЮР ───────────────────────────────────────
    (r"manikyur_medychnyi|medychnyi_manikyur|medychnyi_manikyur_s", "Подологічний педикюр"),

    # ─── ТАЙСЬКИЙ / МЕДОВИЙ МАСАЖ ───────────────────────────────
    (r"masazh_taiskyi|taiskyi_masazh|taj_masazh|thai_massage|masazh_medovyi|medovyi_masazh|honey_massage", "Релакс-масаж"),

    # ─── МАСАЖ НІГ + SPA ────────────────────────────────────────
    (r"masazh_nig_spa|spa_masazh_nig|nig_spa_masazh|masazh_stop_spa|spa_masazh_stop", "SPA-догляд для стоп"),

    # ─── МІКРОНІДЛІНГ / DERMAPEN ────────────────────────────────
    (r"mikronidling|micronidling|microneedling|dermapen|derma_pen|derma_roller", "Мезотерапія"),

    # ─── ПАРАФІНОТЕРАПІЯ ────────────────────────────────────────
    (r"nig_parafinoterapiya|parafinoterapiya_nig|parafin_terapiya_nig", "SPA-догляд для стоп"),
    (r"parafinoterapiya_ruk|ruk_parafinoterapiya|parafin_terapiya_ruk|nmp_rukavychky_spa|rukavychky_nmp_spa|paraffin_gloves", "SPA-догляд для рук"),

    # ─── РЕМОНТ НІГТЯ / ПРОТЕЗУВАННЯ ────────────────────────────
    (r"nigtya_odnogo_remont|odnogo_nigtya_remont|remont_nigtya|nail_repair", "Ремонт нігтя"),
    (r"nigtya_protezuvannya|protezuvannya_nigtya|nigtevoi_protezuvannya|nail_prosthesis", "Протезування нігтьової пластини"),

    # ─── ПАХВОВА область (depil) ────────────────────────────────
    (r"oblast_pakhvova|pakhvova_oblast|pakhva_oblast|pakhvy_zona|pakhva_zona", "Лазерна епіляція"),

    # ─── ОБЛИЧЧЯ ВОСК ───────────────────────────────────────────
    (r"oblychchya_visk|visk_oblychchya|wax_face|face_wax", "Депіляція обличчя"),

    # ─── ОБРОБКА ТОТАЛЬНОГО УРАЖЕННЯ ────────────────────────────
    (r"obrobka_s_shkiry_totalnogo_urazhennya|totalnogo_urazhennya_shkiry|urazhennya_shkiry_stopy|totalne_urazhennya", "Гіперкератоз / Натоптиші / Мозолі"),

    # ─── ОКАНТОВКА ──────────────────────────────────────────────
    (r"okantovka|okantovka_stryzhka|stryzhka_okantovka", "Стрижка жіноча"),

    # ─── ПЕДИКЮР У ПОДОЛОГА ─────────────────────────────────────
    (r"pedykyur_podologa_u|u_podologa_pedykyur|podologa_pedykyur|pedykyur_u_podologa", "Подологічний педикюр"),

    # ─── ПЕРЕВ'ЯЗАННЯ ───────────────────────────────────────────
    (r"perevyazannya_posluga|perevyazannya|perevyazka_posluga|perevyazka", "Тріщини на стопах"),

    # ─── ПЛАСТИНИ Podofix ──────────────────────────────────────
    (r"plastyny_podofix_vstanovlennya|podofix_vstanovlennya|vstanovlennya_podofix|podofix_plastyny|plastyna_podofix", "Врослий ніготь (онихокриптоз)"),

    # ─── ПОКРИТТЯ ТОПОМ ─────────────────────────────────────────
    (r"pokryttya_topom|topom_pokryttya|top_coat|topom_gel|gel_topom", "Покриття гель-лак"),

    # ─── ЯПОНСЬКЕ ПОКРИТТЯ ──────────────────────────────────────
    (r"pokryttya_yaponske|yaponske_pokryttya|yaponske_polirovannya|yaponske_polirovka", "Манікюр японський"),

    # ─── BELOTERO Soft/Volume/Shape ─────────────────────────────
    (r"^soft$|belotero_soft|^volume$|belotero_volume|^shape$|belotero_shape|belotero_intense|belotero_lips|belotero_balance|poverkhnevykh_soft_zalomiv", "Філлери (контурна пластика)"),

    # ─── JUVEDERM Volift / Smile ────────────────────────────────
    (r"volift|smile_ultra|juvederm_volift|juvederm_smile|juvederm_voluma|juvederm_volbella|juvederm_vollure", "Філлери (контурна пластика)"),

    # ─── PRESOTERAPIYA ──────────────────────────────────────────
    (r"presoterapiya|presoterapiia|pressotherapy|presso_terapiya", "Лімфодренажний масаж"),

    # ─── SPYNA chistka/depilation ───────────────────────────────
    (r"^spyna$|spyny_chistka_dod|chastkova_spyna|depilyatsiya_spyna|epilyatsiya_spyna|chistka_spyna", "Чистка спини"),

    # ─── TRYDYNG ────────────────────────────────────────────────
    (r"trydyng|threading|tred|trydyng_briv|trydyng_oblychchya", "Корекція + фарбування брів"),

    # ─── ВИДАЛЕННЯ ЕЛЕМЕНТІВ (papillom-style) ───────────────────
    (r"elementiv_sht_sm_vydalennya|elementu_sht_sm_vydalennya|vydalennya_elementiv|vydalennya_elementu|0_2_5_anestezii|anesteziyeyu_elementu|anesteziyeyu_elementiv", "Видалення міліумів / папілом / бородавок"),

    # ─── НАРОЩУВАННЯ стрічкове ─────────────────────────────────
    (r"naroshchuvannya_strichkove|strichkove_naroshchuvannya|strichka_naroshchuvannya", "Нарощування волосся"),

    # ─── MONALISA Carbon piling tila ────────────────────────────
    (r"karbonovyi_piling|monalisa|carbon_peeling|carbon_piling|piling_tila|tila_piling|diodnym_lazerom_piling", "Пілінг"),

    # ─── BROAD GENERIC (catch-all per topic) ─────────────────────
    # Бородавки (всі форми)
    (r"borodav|borodavk", "Гіперкератоз / Натоптиші / Мозолі"),
    # Мозолі (всі форми)
    (r"mozol|mazol|natopt|trishchin|natoptysh", "Гіперкератоз / Натоптиші / Мозолі"),
    # Tampon валиків / врослий ніготь
    (r"tampon|valyk|valikiv|valykiv|vrosl|vroslyi|vroslogo|onyhokrypt|onikhokrypt|nyt[ -_]?ka|skoba_combiped|combiped|skoby", "Врослий ніготь (онихокриптоз)"),
    # Борода / вуса
    (r"borod|vusa|vusy|barber|barbery", "Борода та вуса"),
    # Брови чоловіча (всі специфічні)
    (r"briv_cholov|cholov.*briv", "Корекція брів чоловіча"),
    # Брови ламінування / керапластика / botox
    (r"briv_laminuvann|laminuvann.*briv|briv_keraplastyka|briv_keroplastyka|briv_dlya_keroplastyka|briv_botoks|botoks_dlya_briv|biofiksats", "Ламінування брів"),
    # Брови корекція / фарбування / перманент
    (r"briv|brovi|brow|eyebrow", "Корекція + фарбування брів"),
    # Вії botox / laminuvann / korektsiya / naroshchuvann
    (r"vii_|_vii|laminuvann.*vii|vii.*laminuvann|vii_botoks|botoks.*vii|lash|naroshch.*vii|korektsiya_vii", "Нарощування вій"),
    # Чоловічі додатково
    (r"cholovich|cholovikam|cholovikiv", "Чоловіча стрижка"),  # default — катастрофа якщо матч першим, але це останній fallback для cholov.
    # Підпил без манікюру / форма
    (r"pidpyl|pidpil|forma_manikyuru|forma_nigtiv|forma_nigot|nail_shape", "Форма нігтів"),
    # Pidnigtovogo / pidnigtovi
    (r"pidnigt|nigtya_mozolya|pidnigtovogo_mozolyu", "Гіперкератоз / Натоптиші / Мозолі"),

    # ─── COSMETOLOGY ─────────────────────────────────────────────
    (r"oblychchya_povnistyu|povnistyu_oblychchya|povne_oblychchya", "Чистка обличчя"),
    # Body sculpting Kuma Shape
    (r"bedra_kuma|kuma_bedra|kuma_shape|kuma_perednya|kuma_zadnya|kuma_vnutrishnya|kuma_zovnishnya|kuma_povnistyu|sidnytsi_kuma|spyna_kuma|zhyvit_kuma|ruka_kuma|stegna_kuma|pidboriddya_kuma|kvk_kuma|kuma_shapes", "Endospheres / Endosphere"),
    # Мікрострумова терапія / ліфтинг апаратний
    (r"dlya_face_lift_liftyng_mikrotokovyi_oblychcha_premium|liftyng_mikrotokovyi|mikrotokovyi_liftyng|premium_lift_face|face_lift_premium|liftyng_face|mikrotokovyi", "Електропорація / Мікрострумова терапія"),
    # Лазерна епіляція додаткові зони
    (r"do_liktya_ruky|do_liktya_rukamy|ruky_do_liktya|nogy_do_kolin|do_kolin_nogy|nogy_chastkovo|chastkovo_nogy|^pakhvy$|^pakhvy | pakhvy$|pakhva|liniya_zhyvota|zhyvota_liniya|^visk |visk_zona|visk_chastina|chastyna_visk|zona_visk", "Лазерна епіляція"),

    # ─── MAKEUP ──────────────────────────────────────────────────
    (r"ekspres_makiyazh|makiyazh_ton_rumyana|makiyazh_rumyana|ekspres_makiazh|express_makiazh|denyi_makiyazh|denyi_makiazh|ekspres_makiyazh_rumyana_ton_vii|makiyazh_ton_vii|art_dekolte_kreatyvnyi_makiyazh|kreatyvnyi_makiyazh|art_makiyazh|art_dekolte_makiyazh", "Макіяж денний"),
    (r"kurs_sam_sobi_vizazhyst|sam_sobi_vizazhyst|urok_sam_sobi|sam_sobi_makiyazh|vizazhyst_sam|kurs_makiyazh_po_sebe|1_3_dlya_kurs_makiyazh_po_sebe|po_sebe_makiyazh|po_sebe_uroky|makiyazh_dlya_sebe", "Урок макіяжу"),
]


def classify(key: str, name_sample: str) -> str | None:
    blob = f"{key} | {name_sample}".lower()
    for pattern, target in MAPPING:
        if re.search(pattern, blob):
            return target
    return None


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--country", default="ua")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Зібрати covered keys (primary + option arrays) per country
            covered_rows = await session.execute(text(f"""
                SELECT canonical_key FROM public.service_profile
                WHERE country='{args.country}' AND canonical_key IS NOT NULL
                UNION
                SELECT jsonb_array_elements_text(o.canonical_keys)
                FROM public.service_profile_option o
                JOIN public.service_profile p ON p.id = o.profile_id
                WHERE p.country='{args.country}' AND o.canonical_keys IS NOT NULL
            """))
            covered = {r[0] for r in covered_rows.all() if r[0]}

            # Зібрати ВСІ canonical_keys цієї country + sample names
            all_rows = await session.execute(text(f"""
                SELECT canonical_key, (array_agg(name ORDER BY name))[1] AS sample, COUNT(*) AS cnt
                FROM {args.country}.service
                WHERE archive=false AND canonical_key IS NOT NULL
                GROUP BY canonical_key
            """))
            uncovered = []
            for key, sample, cnt in all_rows.all():
                if key in covered:
                    continue
                uncovered.append((key, sample, cnt))
            print(f"[{args.country}] Uncovered keys: {len(uncovered)}")

            # Витягнути profile id by name
            profiles = await session.execute(
                select(ServiceProfile.id, ServiceProfile.name)
                .where(ServiceProfile.country == args.country)
            )
            name_to_id = {n: pid for pid, n in profiles.all()}
            print(f"[{args.country}] Profiles available: {len(name_to_id)}")

            from collections import defaultdict
            buckets: dict[str, list[str]] = defaultdict(list)
            unclassified: list[tuple[str, str, int]] = []
            for k, name, cnt in uncovered:
                tgt = classify(k, name or "")
                if tgt:
                    buckets[tgt].append(k)
                else:
                    unclassified.append((k, name, cnt))

            print(f"\nClassified buckets:")
            tot = 0
            for tgt, ks in sorted(buckets.items(), key=lambda x: -len(x[1])):
                print(f"  {tgt:50s} +{len(ks)} keys")
                tot += len(ks)
            print(f"\nTotal classified: {tot}")
            print(f"Unclassified: {len(unclassified)}")
            if unclassified:
                print("\nTop 25 unclassified:")
                for k, n, c in sorted(unclassified, key=lambda x: -x[2])[:25]:
                    print(f"  {c:3d} {k:55s} | {(n or '')[:60]}")

            if not args.apply:
                print("\nDRY RUN")
                return

            updated = 0
            created_profiles = 0
            for tgt, new_keys in buckets.items():
                pid = name_to_id.get(tgt)
                if not pid:
                    # Auto-create minimal placeholder profile
                    placeholder_key = f"auto_{tgt.lower().replace(' ', '_').replace('/', '_')[:60]}"
                    while True:
                        chk = await session.execute(text(
                            "SELECT 1 FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                        ), {"c": args.country, "k": placeholder_key})
                        if not chk.scalar():
                            break
                        placeholder_key = f"auto_{tgt.lower().replace(' ','_')[:50]}_{len(buckets[tgt])}"
                    new_p = ServiceProfile(
                        name=tgt, canonical_key=placeholder_key,
                        country=args.country, default_language="uk", enabled=True,
                        created_by="auto_extend",
                    )
                    session.add(new_p)
                    await session.flush()
                    new_opt = ServiceProfileOption(
                        profile_id=new_p.id, option_type="family",
                        name=tgt, sort_order=0,
                        short_description=f"{tgt} — послуга салону краси.",
                        canonical_keys=[],
                    )
                    session.add(new_opt)
                    await session.flush()
                    pid = new_p.id
                    name_to_id[tgt] = pid
                    created_profiles += 1
                    print(f"  + auto-created profile [{args.country}] {tgt}")

                opt_row = await session.execute(
                    select(ServiceProfileOption)
                    .where(ServiceProfileOption.profile_id == pid)
                    .where(ServiceProfileOption.option_type == "family")
                )
                opt = opt_row.scalar_one_or_none()
                if not opt:
                    print(f"  ⚠ {tgt}: no family option — skip")
                    continue
                current = list(opt.canonical_keys or [])
                merged = list(dict.fromkeys(current + new_keys))
                opt.canonical_keys = merged
                opt.embedding = None
                updated += 1
                print(f"  → {tgt:50s} {len(current)} → {len(merged)}")

            await session.commit()
            print(f"\nDONE: updated {updated} family options. Re-run compute_profile_salons + embed_options.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
