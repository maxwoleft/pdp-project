// Задаём язык приложения (например, "EN", "UA" или "RU")
// Изменяйте это значение в зависимости от версии (/en, /ua, /ru)
const addonRegex = /Add-?on/i;
const HIDE_DURATION_KEYWORDS = [
  'massage',
  'масаж',
  'массаж',
  'endosphere',
  'ендосфера',
  'эндосфера',
  'айкун',
  'стратосфера',
  'stratosphere',
  'icoone'
];
const HIDE_DURATION_EXCLUDE = ['add-on', 'addon'];

/***************************************************
 * Рівні майстрів - ключові слова для визначення рівня
 * Підтримуються EN, UA, RU варіанти та різний регістр
 * ВАЖЛИВО: \b не працює з кирилицею, тому використовуємо (?:^|\s) та (?:\s|$)
 **************************************************/
const MASTER_LEVEL_KEYWORDS = {
  // Junior: EN + UA + RU варіанти (без \b для кирилиці)
  junior: /(?:^|\s)(?:junior|джуніор|джуниор)(?:\s|$)/i,
  // TOP/Senior: EN + UA + RU варіанти
  top: /(?:^|\s)(?:senior|top|топ)(?:\s|$)/i,
  // ART: EN + UA + RU варіанти
  art: /(?:^|\s)(?:art|арт)(?:\s|$)/i
};

// Boundary — пробіл АБО крапка (CRM пише "довж.ТОП" без пробілу теж).
// Включаємо master/майстер/мастер щоб групувати з ТОП/АРТ варіантами.
const LEVEL_SUFFIX_PATTERN = /[\s.]+(?:junior|джуніор|джуниор|senior|top|топ|art|арт|master|майстер|мастер)\s*$/i;

// Visual look-alike normalization для CRM-плутанини розкладок ("AРТ" =
// A-latin + РТ-cyr). Будуємо ОБИДВІ версії — Cyrillic-only та Latin-only —
// бо чисто-латинські слова (Junior/Senior/Master) після Latin→Cyr ламаються.
const _LATIN_TO_CYR = {
  'A':'А','a':'а', 'B':'В','b':'в', 'C':'С','c':'с', 'E':'Е','e':'е',
  'H':'Н','h':'н', 'I':'І','i':'і', 'K':'К','k':'к', 'M':'М','m':'м',
  'O':'О','o':'о', 'P':'Р','p':'р', 'R':'Р','r':'р', 'S':'С','s':'с',
  'T':'Т','t':'т', 'X':'Х','x':'х', 'Y':'У','y':'у'
};
const _CYR_TO_LAT = Object.fromEntries(Object.entries(_LATIN_TO_CYR).map(([l, c]) => [c, l]));
function _normCyr(s) {
  return (s || '').replace(/[A-Za-z]/g, ch => _LATIN_TO_CYR[ch] || ch);
}
function _normLat(s) {
  return (s || '').replace(/./g, ch => _CYR_TO_LAT[ch] || ch);
}

/**
 * Витягує базову назву послуги без суфіксів рівня майстра
 * Наприклад: "Манікюр ТОП" -> "Манікюр", "Manicure Junior" -> "Manicure"
 * Підтримує EN, UA, RU та різний регістр
 */
function _normalizeBaseName(s) {
  // Нормалізуємо whitespace навколо punctuation щоб варіанти типу
  // "X / Y" та "X /Y" мали однаковий baseName (CRM-typo проти угод).
  return (s || '')
    .replace(/\s*\/\s*/g, ' / ')
    .replace(/\s*\+\s*/g, ' + ')
    .replace(/\s*-\s*/g, ' - ')
    .replace(/\s+/g, ' ')
    .trim();
}

function getBaseServiceName(serviceName) {
  if (!serviceName) return '';
  // Тестуємо суфікс на обох нормалізаціях (Latin-only + Cyrillic-only).
  // Char-by-char — index в normalized = index в original.
  const cyr = _normCyr(serviceName);
  const lat = _normLat(serviceName);
  const m = cyr.match(LEVEL_SUFFIX_PATTERN) || lat.match(LEVEL_SUFFIX_PATTERN);
  let base;
  if (m && typeof m.index === 'number') {
    // Стрипаємо trailing пунктуацію/пробіли — щоб "...3 довж." та "...3 довж"
    // звелися до однакового baseName.
    base = serviceName.slice(0, m.index).replace(/[\s.,\-+:;]+$/, '').trim();
  } else {
    base = serviceName.trim();
  }
  return _normalizeBaseName(base);
}

/**
 * Визначає рівень майстра з назви послуги
 * Повертає: 'junior', 'top', 'art' або 'master' (звичайний)
 * Підтримує EN, UA, RU та різний регістр
 * ВАЖЛИВО: \b не працює з кирилицею, тому перевіряємо через пробіли або кінець рядка
 */
function getMasterLevelFromServiceName(serviceName) {
  if (!serviceName) return 'master';
  // Перевіряємо паттерн на обох нормалізаціях. Це покриває:
  //   "Junior" (pure Latin)  → /junior/ matches normLat
  //   "AРТ" (mixed)          → /арт/  matches normCyr
  //   "Senior" (pure Latin)  → /senior/ matches normLat
  const lowerCyr = _normCyr(serviceName).toLowerCase();
  const lowerLat = _normLat(serviceName).toLowerCase();
  const match = (re) => re.test(lowerCyr) || re.test(lowerLat);

  if (match(/(?:^|[\s.])(?:junior|джуніор|джуниор)(?:\s|$)/)) return 'junior';
  if (match(/(?:^|[\s.])(?:senior|top|топ)(?:\s|$)/)) return 'top';
  if (match(/(?:^|[\s.])(?:art|арт)(?:\s|$)/)) return 'art';
  if (match(/(?:^|[\s.])(?:master|майстер|мастер)(?:\s|$)/)) return 'master';

  return 'master';
}

/**
 * Отримує локалізовану назву рівня майстра
 */
function getMasterLevelLabel(level, lang) {
  const labels = {
    junior: { en: 'Junior', ua: 'Junior', ru: 'Junior' },
    master: { en: 'Master', ua: 'Майстер', ru: 'Мастер' },
    top: { en: 'TOP', ua: 'ТОП', ru: 'ТОП' },
    art: { en: 'ART', ua: 'АРТ', ru: 'АРТ' }
  };
  const langKey = (lang || 'en').toLowerCase();
  return labels[level]?.[langKey] || labels[level]?.en || level;
}

/**
 * Групує послуги за базовою назвою (об'єднує варіанти з різними рівнями майстрів)
 * ВАЖЛИВО: Групуємо ТІЛЬКИ якщо є послуги з різними рівнями майстрів!
 * Наприклад: "Манікюр" + "Манікюр ТОП" = група з hasVariants=true
 * Але "Манікюр (короткі)" + "Манікюр (середні)" = НЕ група (різні послуги)
 *
 * Повертає масив об'єктів:
 * {
 *   baseName: string,           // Базова назва без рівня
 *   displayName: string,        // Назва для відображення
 *   minPrice: number,           // Мінімальна ціна (для "Від X")
 *   hasVariants: boolean,       // Чи є варіанти з РІЗНИМИ рівнями майстрів
 *   variants: Array,            // Масив варіантів послуг з різними рівнями
 *   baseService: Object         // Базова послуга для відображення
 * }
 */
function groupServicesByLevel(services, lang) {
  if (!services || services.length === 0) return [];

  const groups = new Map();

  services.forEach(service => {
    const fullName = extractTextByLang(service.name, lang);
    const baseName = getBaseServiceName(fullName);
    // Визначаємо рівень з локалізованої назви (fullName), а не з повної мультимовної
    const level = getMasterLevelFromServiceName(fullName);

    if (!groups.has(baseName)) {
      groups.set(baseName, {
        baseName: baseName,
        displayName: fullName, // Зберігаємо повну назву першої послуги
        minPrice: service.location_prices,
        maxPrice: service.location_prices,
        variants: [],
        baseService: service,
        levels: new Set() // Для відстеження унікальних рівнів
      });
    }

    const group = groups.get(baseName);
    group.levels.add(level);
    group.variants.push({
      ...service,
      level: level,
      levelLabel: getMasterLevelLabel(level, lang),
      displayName: fullName
    });

    // Оновлюємо мін/макс ціну та базову послугу
    if (service.location_prices < group.minPrice) {
      group.minPrice = service.location_prices;
      group.baseService = service;
    }
    if (service.location_prices > group.maxPrice) {
      group.maxPrice = service.location_prices;
    }
  });

  // Перевіряємо чи є РІЗНІ рівні майстрів (не просто кілька послуг)
  // hasVariants = true ТІЛЬКИ якщо є хоча б один рівень відмінний від іншого
  groups.forEach(group => {
    // Є варіанти ТІЛЬКИ якщо є більше одного унікального рівня
    // АБО якщо є рівень НЕ "master" (значить є базова версія десь ще)
    const hasNonMasterLevel = group.variants.some(v => v.level !== 'master');
    const hasMasterLevel = group.variants.some(v => v.level === 'master');

    // Варіанти є тільки якщо:
    // 1. Є і звичайний майстер і спеціалізований (TOP/ART/Junior)
    // 2. АБО є кілька різних спеціалізованих рівнів
    group.hasVariants = (hasNonMasterLevel && hasMasterLevel) || (group.levels.size > 1 && hasNonMasterLevel);
    group.variants.sort((a, b) => a.location_prices - b.location_prices);
  });

  return Array.from(groups.values());
}

/**
 * Функция для извлечения текста по языку из составной строки.
 * Пример строки: "EN 2D Volume Lashes / UA Нарощування вій 2D / RUS Наращивание ресниц 2D"
 * Для RU ищутся оба варианта: "RU" и "RUS".
 */
function extractTextByLang(fullText, lang) {
  // Удаляем невидимые BOM, zero-width пробелы и т. п.
  fullText = fullText.replace(/[\uFEFF\u200B\u200C\u200D\u200E\u200F]/g, "");

  if (!fullText) return "";
  
  // 1) Заменяем HTML-сущность &#x2F; на обычный "/"
  fullText = fullText.replace(/&#x2F;/g, "/");

  lang = lang.toUpperCase();
  let pattern;
  if (lang === "RU") {
    pattern = /(?:RU|RUS)\s+([^/]+)/i;
  } else {
    pattern = new RegExp(lang + "\\s+([^/]+)", "i");
  }
  let match = fullText.match(pattern);
  if (match && match[1]) {
    return match[1].trim();
  }
  return fullText;
}

function extractTextByLangDescription(fullText, lang) {
 // Удаляем невидимые BOM, zero-width пробелы и т. п.
  fullText = fullText.replace(/[\uFEFF\u200B\u200C\u200D\u200E\u200F]/g, "");
  if (!fullText) return "";

  // Заменяем HTML-сущность &#x2F; на обычный "/"
  fullText = fullText.replace(/&#x2F;/g, "/");

  const sections = {};
  const sectionRegex = /(EN|UA|RU|RUS|PL)\s+([\s\S]*?)(?=(?:[\s\/|,-]*(?:EN|UA|RU|RUS|PL)\s+)|$)/g;
  let match;
  while ((match = sectionRegex.exec(fullText)) !== null) {
    const key = match[1] === "RUS" ? "RU" : match[1];
    sections[key] = match[2].trim();
  }

  const normalizedLang = lang.toUpperCase() === "RUS" ? "RU" : lang.toUpperCase();
  let result = sections[normalizedLang];
  if (!result) {
    return fullText;
  }

  // Заменяем символы разделителей на HTML-переносы строк
  result = result
    .replace(/\//g, "<br>")
    .replace(/\r?\n/g, "<br>");

  return result;
}


function fixMalformedHtml(str) {
  if (!str) return "";
  // Заменяем странный фрагмент <br < div> на нормальный <br />
    return str.replace(/<br\s+<\s*div>/gi, "");
}



/************************************
 * Глобальный массив для хранения выбранных услуг
 ************************************/
let selectedServices = [];
let categoriesData = []; // Переменная становится глобальной

/**
 * Функция для загрузки данных о сотрудниках
 * @returns {Promise<Array>} - Возвращает массив сотрудников
 */
async function loadEmployees() {
  try {
    const salon = window.salon || 'l1';
    const response = await fetch(`/api/salons/${salon}/employees`);
    if (!response.ok) {
      throw new Error('Не удалось загрузить данные о сотрудниках.');
    }
    return await response.json();
  } catch (error) {
    console.error(error);
    return [];
  }
}

/***************************************************
 * 1) Массив опций для длины волос
 **************************************************/
// `suffixes[lang]` — array of substrings; service name матчиться, якщо містить
// хоча б один. Numbered варіанти ("1 довжина" ... "5 довжина") уживаються
// в UA/PL салонах (Bucha/Kyiv/Warsaw/etc), London/UA-london — descriptive.
// Units per country: GB → inches, UA/PL → cm.
// 1" ≈ 2.54cm; conversions: 2"→5, 6"→15, 10"→25, 14"→35, 20"→50, 24"→60.
const HAIR_LENGTH_OPTIONS = [
  {
    id: "short",
    suffixes: {
      en: ["(Short)", "1 length", "1length"],
      ua: ["(Коротке волосся)", "1 довжина", "1довжина"],
      ru: ["(Короткие волосы)", "1 длина", "1длина"]
    },
    range_in: "2 - 6\"",
    range_cm: "5 - 15 см",
    image: "/static/img/hair-length-short.png"
  },
  {
    id: "medium",
    suffixes: {
      en: ["(Medium)", "2 length", "2length"],
      ua: ["(Середнє волосся)", "(Середня довжина)", "2 довжина", "2довжина"],
      ru: ["(Средние волосы)", "2 длина", "2длина"]
    },
    range_in: "6 - 10\"",
    range_cm: "15 - 25 см",
    image: "/static/img/hair-length-medium.png"
  },
  {
    id: "long",
    suffixes: {
      en: ["(Long)", "3 length", "3length"],
      ua: ["(Довге волосся)", "3 довжина", "3довжина"],
      ru: ["(Длинные волосы)", "3 длина", "3длина"]
    },
    range_in: "10 - 14\"",
    range_cm: "25 - 35 см",
    image: "/static/img/hair-length-long.png"
  },
  {
    id: "extraLong",
    suffixes: {
      en: ["(Extra Long)", "(Extra long)", "4 length", "4length"],
      ua: ["(Дуже довге волосся)", "4 довжина", "4довжина"],
      ru: ["(Очень длинные волосы)", "4 длина", "4длина"]
    },
    range_in: "14 - 20\"",
    range_cm: "35 - 50 см",
    image: "/static/img/hair-length-extralong.png"
  },
  {
    id: "tailbone",
    suffixes: {
      en: ["(Tailbone length)", "5 length", "6 length", "7 length", "8 length"],
      ua: ["(Довжина до куприка)", "5 довжина", "5довжина", "6 довжина", "7 довжина", "8 довжина"],
      ru: ["(Длина до копчика)", "5 длина", "6 длина", "7 длина", "8 длина"]
    },
    range_in: "20 - 24\"+",
    range_cm: "50 см+",
    image: "/static/img/hair-length-tailbonelength.png"
  }
];

// Country-aware unit picker. GB → inches. UA/PL та інші → cm.
function _hairRange(opt) {
  const country = (window.salonCountry || '').toLowerCase();
  if (country === 'gb' || country === 'uk') return opt.range_in;
  return opt.range_cm;
}

// Currency symbol per salon country (GB→£, UA→₴, PL→zł).
function _currencySymbol() {
  const c = (window.salonCountry || '').toLowerCase();
  if (c === 'ua') return '₴';
  if (c === 'pl') return 'zł';
  return '£';
}
window._currencySymbol = _currencySymbol;

// Helper: array OR string → array of strings
function _sufArr(opt, lang) {
  const v = (opt.suffixes || {})[lang];
  if (!v) return [];
  return Array.isArray(v) ? v : [v];
}
// Усі суфікси опції з усіх мов (lang-agnostic match). User-вимога: правила
// фільтру однакові незалежно від поточної мови UI — щоб service з UA-назвою
// "1 довжина" попадав у Short коли user в EN режимі тощо.
function _allSuf(opt) {
  const out = [];
  const seen = new Set();
  for (const langKey of Object.keys(opt.suffixes || {})) {
    for (const s of _sufArr(opt, langKey)) {
      if (s && !seen.has(s)) { seen.add(s); out.push(s); }
    }
  }
  return out;
}
function _nameMatchesAny(name, suffixes) {
  const lower = name.toLowerCase();
  return suffixes.some(s => s && lower.includes(s.toLowerCase()));
}
// Non-hair services що використовують "X довжина" в назві в іншому контексті
// (довжина нігтя, масажні зони тощо). Виключаємо їх з hair-length detection.
// STEMS (Cyrillic prefix + слово); WORDS (exact-match Latin/short).
const _NAIL_STEMS = ['нігт', 'манік', 'педик', 'масаж', 'массаж', 'масс'];
const _NAIL_WORDS = [
  'manicure', 'pedicure', 'nail',
  'paznok', 'paznokc',
  'полігель', 'гель', 'гелем', 'gel',
  'massage', 'massaż'
];
const _BOUNDARY_LEFT = '(^|[\\s/,.+(\\-])';
const _BOUNDARY_RIGHT = '(?=$|[\\s/,.+)\\-])';
const _CYR_WORD = '[а-яА-ЯіїєґІЇЄҐ]*';
const _NAIL_STEMS_RE = new RegExp(
  _BOUNDARY_LEFT + '(?:' + _NAIL_STEMS.join('|') + ')' + _CYR_WORD + _BOUNDARY_RIGHT,
  'i'
);
const _NAIL_WORDS_RE = new RegExp(
  _BOUNDARY_LEFT + '(?:' + _NAIL_WORDS.join('|') + ')' + _BOUNDARY_RIGHT,
  'i'
);
function _isNailService(name) {
  const s = name || '';
  return _NAIL_STEMS_RE.test(s) || _NAIL_WORDS_RE.test(s);
}

// Category-level skip — для категорій типу "Нігтьовий сервіс" блокуємо весь
// hair-length detection незалежно від назв послуг (бо там може бути "Укріплення
// X довжина" — це довжина нігтя, не волосся).
const _NON_HAIR_CAT_RE = /нігт|nail|манік|manicur|педик|pedicur|масаж|массаж|massa[zż]/i;
function _isNonHairCategoryName(name) {
  // name може бути string OR multi-lang dict ("EN HAIR / UA Волосся / RUS Волосы")
  if (!name) return false;
  const text = typeof name === 'string'
    ? name
    : Object.values(name).filter(v => typeof v === 'string').join(' ');
  return _NON_HAIR_CAT_RE.test(text);
}
function _serviceHasHairLength(name, suffixes) {
  if (_isNailService(name)) return false;
  return _nameMatchesAny(name, suffixes);
}


/***************************************************
 * 2) Глобальные переменные выбора
 **************************************************/
let selectedHairLength = null;

// Глобальный список главных категорий (приходит из booking.html через window.categoriesData)
window.categoriesData = window.categoriesData || [];

// Глобальный массив отфильтрованных категорий (по уровню мастера)
let filteredCategories = [];

// Текущая выбранная категория
window.currentCategory = null;

// Флаг для скрытия длительности услуг в текущей категории
let hideDurationForCurrentCategory = false;

// Категория из параметров URL (id или название)
const urlParams = new URLSearchParams(window.location.search);
const initialCategoryParam = urlParams.get('category');

/***************************************************
 * 2a) Завантаження даних категорій з сервера
 **************************************************/
const categoryLoadPromises = new Map();

function showLoadingOverlay() {
  const overlay = document.getElementById('loadingOverlay');
  if (overlay) {
    overlay.style.display = 'flex';
  }
}

function hideLoadingOverlay() {
  const overlay = document.getElementById('loadingOverlay');
  if (overlay) {
    overlay.style.display = 'none';
  }
}

function markCategoriesAsUnloaded(list) {
  if (!Array.isArray(list)) return;
  list.forEach(cat => {
    if (!Array.isArray(cat.children)) {
      cat.children = [];
    }
    if (!Array.isArray(cat.services)) {
      cat.services = [];
    }
    cat.__servicesLoaded = false;
    markCategoriesAsUnloaded(cat.children);
  });
}

function markSubtreeLoaded(node) {
  if (!node) return;
  if (!Array.isArray(node.services)) {
    node.services = [];
  }
  if (!Array.isArray(node.children)) {
    node.children = [];
  }
  node.__servicesLoaded = true;
  node.children.forEach(child => {
    markSubtreeLoaded(child);
  });
}

function updateCategorySubtree(catId, newData, list = window.categoriesData) {
  if (!Array.isArray(list)) return false;
  for (let i = 0; i < list.length; i++) {
    const node = list[i];
    if (node.id === catId) {
      Object.assign(node, newData);
      node.children = newData.children || [];
      node.services = newData.services || [];
      markSubtreeLoaded(node);
      return true;
    }
    if (node.children && node.children.length > 0) {
      const updated = updateCategorySubtree(catId, newData, node.children);
      if (updated) {
        return true;
      }
    }
  }
  return false;
}

async function loadCategoriesForSalon(salon) {
  const response = await fetch(`/api/salons/${salon}/categories`);
  if (!response.ok) {
    throw new Error('Не удалось загрузить список категорий');
  }
  const data = await response.json();
  window.categoriesData = Array.isArray(data) ? data : [];
  markCategoriesAsUnloaded(window.categoriesData);
  filteredCategories = [];
}

async function ensureCategoryServices(catId) {
  const existing = findCategoryById(catId, window.categoriesData);
  if (!existing) {
    return null;
  }
  if (existing.__servicesLoaded) {
    return existing;
  }

  if (categoryLoadPromises.has(catId)) {
    await categoryLoadPromises.get(catId);
    return findCategoryById(catId, window.categoriesData);
  }

  const loadPromise = (async () => {
    const salonCode = window.salon || 'l1';
    const response = await fetch(`/api/salons/${salonCode}/categories/${catId}`);
    if (!response.ok) {
      throw new Error('Не удалось загрузить данные категории');
    }
    const data = await response.json();
    updateCategorySubtree(catId, data, window.categoriesData);
  })();

  categoryLoadPromises.set(catId, loadPromise);
  try {
    await loadPromise;
  } finally {
    categoryLoadPromises.delete(catId);
  }

  return findCategoryById(catId, window.categoriesData);
}

/***************************************************
 * 3) Логіка рівнів майстрів видалена - тепер рівень визначається автоматично
 *    з варіантів послуги при виборі часу
 **************************************************/

// Удаляем категории и подкатегории, в названии которых встречается "Add-on"
function filterCategoriesNoAddon(list) {
  if (!Array.isArray(list)) return [];
  return list
    .filter(cat => !addonRegex.test(cat.name))
    .map(cat => ({
      ...cat,
      children: filterCategoriesNoAddon(cat.children || [])
    }));
}


function normalizeCategoryName(name) {
  if (!name) return '';
  if (typeof name === 'string') {
    return name.toLowerCase();
  }
  if (typeof name === 'object') {
    return Object.values(name)
      .filter(value => typeof value === 'string')
      .join(' ')
      .toLowerCase();
  }
  return String(name).toLowerCase();
}

function hasHideDurationKeyword(name) {
  const normalized = normalizeCategoryName(name);
  if (!normalized) {
    return false;
  }
  if (HIDE_DURATION_EXCLUDE.some(exclude => normalized.includes(exclude))) {
    return false;
  }
  return HIDE_DURATION_KEYWORDS.some(keyword => normalized.includes(keyword));
}

function shouldHideDurationForService(service) {
  if (hideDurationForCurrentCategory) {
    return true;
  }
  if (!service || !service.name) {
    return false;
  }
  return hasHideDurationKeyword(service.name);
}

// Проверяем, является ли категория основной категорией массажей
function isMassageCategory(category) {
  if (!category) return false;
  if (hasHideDurationKeyword(category.name)) {
    return true;
  }
  if (Array.isArray(category.children)) {
    return category.children.some(child => isMassageCategory(child));
  }
  return false;
}


// function getFilteredCategoriesByMasterLevel(masterLevel) {
//   const filtered = [];
//   window.categoriesData.forEach(cat => {
//     const filteredChildren = filterChildrenByMasterLevel(cat.children || [], masterLevel);
//     if (
//       filteredChildren.length > 0 ||
//       (masterLevel === "Мастер" && ((cat.services && cat.services.length > 0) || (cat.children && cat.children.length > 0)))
//     ) {
//       const newCat = { ...cat, children: filteredChildren };
//       filtered.push(newCat);
/***************************************************
 * 4) Логика выбора длины волос
 **************************************************/
function hasHairLengthServices(category, depth) {
  // Limit рекурсії на 1 рівень: показуємо length-фільтр тільки коли в самій
  // category або у її прямих children є services з length-суфіксом.
  // Глибший пошук давав false-positives (top-level "Чоловіки" має child
  // "Волосся" з sub-subs які містять "довжина" — фільтр з'являвся на верху).
  if (depth === undefined) depth = 0;
  if (!category) return false;
  // Category-level skip — для nail/massage категорій блокуємо весь scan
  // (там "X довжина" — це довжина нігтя, не волосся).
  if (_isNonHairCategoryName(category.name)) return false;
  const allSuffixes = HAIR_LENGTH_OPTIONS.flatMap(_allSuf);

  if ((category.services || []).some(s => {
    const localizedName = extractTextByLang(s.name, window.appLang);
    return _serviceHasHairLength(localizedName, allSuffixes);
  })) {
    return true;
  }

  if (depth >= 1) return false;
  if (category.children && category.children.length > 0) {
    return category.children.some(child => hasHairLengthServices(child, depth + 1));
  }
  return false;
}


// Отрисовка опций выбора длины волос с учётом мультиязычности
function renderHairLengthOptions() {
  const container = document.getElementById("hairLengthOptions");
  if (!container) return;

  if (container.children.length > 0) {
    updateSelectedOption();
    return;
  }
  
  const currentLang = window.appLang ? window.appLang.toLowerCase() : 'en';
  
  HAIR_LENGTH_OPTIONS.forEach(opt => {
    const div = document.createElement("div");
    div.classList.add("hairLengthOption");
    div.dataset.id = opt.id;

    const checkImg = document.createElement("img");
    checkImg.classList.add("check");
    checkImg.style.display = "none";
    checkImg.src = check; // убедитесь, что переменная check определена
    div.appendChild(checkImg);

    const img = document.createElement("img");
    img.src = opt.image;
    img.alt = opt.id;
    div.appendChild(img);

    const p = document.createElement("p");
    p.textContent = _hairRange(opt);
    div.appendChild(p);

    div.addEventListener("click", () => {
      selectedHairLength = opt.id;
      updateSelectedOption();
      handleHairLengthSelection();
    });

    container.appendChild(div);
  });

  updateSelectedOption();
}


function updateSelectedOption() {
  const options = document.querySelectorAll("#hairLengthOptions .hairLengthOption");
  options.forEach(div => {
    if (div.dataset.id === selectedHairLength) {
      div.classList.add("selected");
      div.querySelector(".check").style.display = "block";
    } else {
      div.classList.remove("selected");
      div.querySelector(".check").style.display = "none";
    }
  });
}

/***************************************************
 * 5) Фильтрация услуг по выбранной длине волос
 * Фільтрація за рівнем майстра видалена - тепер всі рівні показуються згрупованими
 **************************************************/
function filterServices(services) {
  if (!services) return [];
  return services.filter(service => {
    const localizedName = extractTextByLang(service.name, window.appLang);
    const hasAnySuffix = HAIR_LENGTH_OPTIONS.some(o =>
      _serviceHasHairLength(localizedName, _allSuf(o))
    );
    if (selectedHairLength) {
      const opt = HAIR_LENGTH_OPTIONS.find(o => o.id === selectedHairLength);
      if (opt && hasAnySuffix && !_serviceHasHairLength(localizedName, _allSuf(opt))) {
        return false;
      }
    }
    return true;
  });
}





/***************************************************
 * 6) Рендер категории (в центральной колонке)
 * З групуванням послуг за рівнем майстра
 **************************************************/
function renderCategoryHTML(category) {
  const allServices = category.services || [];
  const filteredByHairLength = filterServices(allServices);
  const groupedServices = groupServicesByLevel(filteredByHairLength, window.appLang);

  let html = `<h2>${extractTextByLang(category.name, window.appLang)}</h2>`;

  if (groupedServices.length > 0) {
    html += `<div class="services-block">`;
    groupedServices.forEach(group => {
      const serv = group.baseService;
      let rawDescription = serv.description || "";
      let saleMatch = rawDescription.match(/\bSALE(\d+(?:\.\d{1,2})?)\b/i);
      if (saleMatch) {
        rawDescription = rawDescription.replace(/\s*\bSALE\d+(?:\.\d{1,2})?\b\s*/i, " ").trim();
      }
      // Якщо є варіанти - показуємо базову назву, інакше - повну назву послуги
      let serviceName = group.hasVariants ? group.baseName : extractTextByLang(serv.name, window.appLang);
      let serviceDescription = extractTextByLangDescription(rawDescription, window.appLang);

      let oldPriceHtml = "";
      if (saleMatch) {
        const oldPrice = parseFloat(saleMatch[1]);
        if (!isNaN(oldPrice)) {
          oldPriceHtml = `<span class="service-price old-price">${oldPrice} ${_currencySymbol()}</span>`;
        }
      }

      const hideDuration = shouldHideDurationForService(serv);
      const durationHtml = hideDuration
        ? ""
        : `<span class="service-duration">${serv.duration} ${window.translations["indDurationService"]}</span>`;

      // "Від" prefix тільки якщо ціни різні (range), інакше — просто число.
      const pricePrefix = (group.hasVariants && group.maxPrice > group.minPrice)
        ? (window.translations["priceFrom"] || "From") + " "
        : "";
      const priceHtml = `<span class="service-price">${pricePrefix}${group.minPrice} ${_currencySymbol()}</span>`;

      const variantsJson = encodeURIComponent(JSON.stringify(group.variants.map(v => ({
        id: v.id,
        name: v.name,
        duration: v.duration,
        location_prices: v.location_prices,
        price_currency: v.price_currency,
        description: v.description,
        location_position: v.location_position,
        category: v.category,
        level: v.level,
        levelLabel: v.levelLabel
      }))));

      html += `
        <div class="service-row"
             data-location-position="${serv.location_position}"
             data-category="${serv.category}"
             data-has-variants="${group.hasVariants}"
             data-variants="${variantsJson}">
          <div class="service-header">
            <div class="accordion-toggle">
              <img src="${arrowdown}" alt="Toggle Accordion">
            </div>
            <div class="service-name_duration">
              <span class="service-name">${serviceName}</span>
              ${durationHtml}
            </div>
            <div class="priceAndBtnAdd">
              ${oldPriceHtml}${priceHtml}
              <button class="service-add pricing-service__action-icon"
                data-id="${serv.id}"
                data-service-name="${serviceName}"
                data-duration="${serv.duration}"
                data-location_prices="${serv.location_prices}"
                data-price_currency="${serv.price_currency}"
                data-description="${serv.description}"
                data-location-position="${serv.location_position}"
                data-category="${serv.category}"
                data-has-variants="${group.hasVariants}"
                data-variants="${variantsJson}">
                <img class="plus" src="${plus}">
              </button>
            </div>
          </div>
          <div class="service-description" style="display: none;">
            ${serviceDescription}
          </div>
        </div>
      `;
    });
    html += `</div>`;
  }

  if (category.children && category.children.length > 0) {
    let subHtml = "";
    category.children.forEach(subcat => {
      subHtml += renderSubcategoryHTML(subcat);
    });
    if (subHtml.trim() !== "") {
      html += `<div class="subcategories">${subHtml}</div>`;
    }
  }

  return html;
}

// Аналогично исправляем функцию для подкатегорий
function renderSubcategoryHTML(subcat) {
  // Спочатку фільтруємо за довжиною волосся, потім групуємо
  const allServices = subcat.services || [];
  const filteredByHairLength = filterServices(allServices);
  const groupedServices = groupServicesByLevel(filteredByHairLength, window.appLang);

  let childrenHtml = "";
  if (subcat.children && subcat.children.length > 0) {
    subcat.children.forEach(child => {
      childrenHtml += renderSubcategoryHTML(child);
    });
  }

  if (groupedServices.length === 0 && childrenHtml.trim() === "") {
    return "";
  }
  let html = `
    <div class="subcategory-item">
      <h3 class="subcategory-title">${extractTextByLang(subcat.name, window.appLang)}</h3>
  `;

  if (groupedServices.length > 0) {
    html += `<div class="services-block">`;
    groupedServices.forEach(group => {
      const serv = group.baseService;
      let rawDescription = serv.description || "";
      let saleMatch = rawDescription.match(/\bSALE(\d+(?:\.\d{1,2})?)\b/i);
      if (saleMatch) {
        rawDescription = rawDescription.replace(/\s*\bSALE\d+(?:\.\d{1,2})?\b\s*/i, " ").trim();
      }

      // Якщо є варіанти - показуємо базову назву, інакше - повну назву послуги
      let serviceName = group.hasVariants ? group.baseName : extractTextByLang(serv.name, window.appLang);
      let serviceDescription = extractTextByLangDescription(rawDescription, window.appLang);

      let oldPriceHtml = "";
      if (saleMatch) {
        const oldPrice = parseFloat(saleMatch[1]);
        if (!isNaN(oldPrice)) {
          oldPriceHtml = `<span class="service-price old-price">${oldPrice} ${_currencySymbol()}</span>`;
        }
      }

      const hideDuration = shouldHideDurationForService(serv);
      const durationHtml = hideDuration
          ? ""
          : `<span class="service-duration">${serv.duration} ${window.translations["indDurationService"]}</span>`;

      // Якщо є варіанти з різними рівнями - показуємо "Від X"
      // "Від" prefix тільки якщо ціни різні (range), інакше — просто число.
      const pricePrefix = (group.hasVariants && group.maxPrice > group.minPrice)
        ? (window.translations["priceFrom"] || "From") + " "
        : "";
      const priceHtml = `<span class="service-price">${pricePrefix}${group.minPrice} ${_currencySymbol()}</span>`;

      // Зберігаємо всі варіанти як JSON
      const variantsJson = encodeURIComponent(JSON.stringify(group.variants.map(v => ({
        id: v.id,
        name: v.name,
        duration: v.duration,
        location_prices: v.location_prices,
        price_currency: v.price_currency,
        description: v.description,
        location_position: v.location_position,
        category: v.category,
        level: v.level,
        levelLabel: v.levelLabel
      }))));

      html += `
        <div class="service-row"
             data-id="${serv.id}"
             data-location-position="${serv.location_position}"
             data-category="${serv.category}"
             data-has-variants="${group.hasVariants}"
             data-variants="${variantsJson}">
          <div class="service-header">
            <div class="accordion-toggle">
              <img src="${arrowdown}" alt="Toggle Accordion">
            </div>
            <div class="service-name_duration">
              <span class="service-name">${serviceName}</span>
              ${durationHtml}
            </div>
            <div class="priceAndBtnAdd">
              ${oldPriceHtml}${priceHtml}
              <button class="service-add"
                data-id="${serv.id}"
                data-service-name="${serviceName}"
                data-duration="${serv.duration}"
                data-location_prices="${serv.location_prices}"
                data-price_currency="${serv.price_currency}"
                data-description="${serv.description}"
                data-location-position="${serv.location_position}"
                data-category="${serv.category}"
                data-has-variants="${group.hasVariants}"
                data-variants="${variantsJson}">
                <img class="plus" src="${plus}">
              </button>
            </div>
          </div>
          <div class="service-description" style="display: none;">
            ${serviceDescription}
          </div>
        </div>
      `;
    });
    html += `</div>`;
  }

  html += childrenHtml;

  html += `</div>`;
  return html;
}


/***************************************************
 * 7) Инициализация аккордеонов
 **************************************************/
function initAccordionToggles() {
  const toggles = document.querySelectorAll(".accordion-toggle");
  toggles.forEach(toggle => {
    toggle.addEventListener("click", function() {
      const serviceRow = this.closest(".service-row");
      if (!serviceRow) return;
      const descriptionEl = serviceRow.querySelector(".service-description");
      if (!descriptionEl) return;
      if (descriptionEl.style.display === "none" || descriptionEl.style.display === "") {
        descriptionEl.style.display = "block";
        serviceRow.style.background = "#0000000D";
        const img = this.querySelector("img");
        if (img) {
          img.src = arrowup;
        }
      } else {
        descriptionEl.style.display = "none";
        serviceRow.style.background = "";
        const img = this.querySelector("img");
        if (img) {
          img.src = arrowdown;
        }
      }
    });
  });
}

/***************************************************
 * Вспомогательная функция для сброса содержимого middleSide
 **************************************************/
function resetMiddleSide() {
  const middleSide = document.querySelector(".middleSide");
  if (middleSide) {
    middleSide.innerHTML = `
      <h2 class="service-title">${window.translations["servicesTitle"]}</h2>
      <div class="hairLengthSelector" id="hairLengthSelector" style="display: none;">
        <h3>${window.translations["lengthHairText"]}</h3>
        <div class="hairLengthOptions" id="hairLengthOptions">
          <!-- Иконки добавит JS -->
        </div>
      </div>
      <div id="hairLengthDropdownContainer" style="display:none;"></div>
      <div class="services-block" id="servicesList">
        <!-- Сюда JS добавит услуги -->
      </div>
    `;
  }
}

/***************************************************
 * 8) Отображение категории с модифицированной логикой выбора длины волос
 **************************************************/
function renderAndShowCategory(category) {
  if (!window.currentCategory || window.currentCategory.id !== category.id) {
    selectedHairLength = null;
  }
  window.currentCategory = category;
  hideDurationForCurrentCategory = isMassageCategory(category);
  const selectorDiv = document.getElementById("hairLengthSelector");
  const dropdownContainer = document.getElementById("hairLengthDropdownContainer");
  const servicesDiv = document.getElementById("servicesList");

  if (hasHairLengthServices(category)) {
    if (selectorDiv) selectorDiv.style.display = "block";
    if (dropdownContainer) dropdownContainer.style.display = "none";
    if (servicesDiv) servicesDiv.style.display = "none";
    renderHairLengthOptions();
  } else {
    if (selectorDiv) selectorDiv.style.display = "none";
    if (dropdownContainer) dropdownContainer.style.display = "none";
    if (servicesDiv) servicesDiv.style.display = "block";
    selectedHairLength = null;
    renderServices(category);
  }
}

/***************************************************
 * 9) Функция для рендеринга перечня услуг
 * Тепер з групуванням послуг за рівнем майстра
 **************************************************/
function renderServices(category) {
  let html = "";
  // Спочатку фільтруємо за довжиною волосся (якщо вибрано)
  const allServices = category.services || [];
  const filteredByHairLength = filterServices(allServices);

  // Групуємо послуги за базовою назвою (без суфіксів рівня)
  const groupedServices = groupServicesByLevel(filteredByHairLength, window.appLang);

  if (groupedServices.length > 0) {
    html += `<div class="services-block">`;
    groupedServices.forEach(group => {
      // Беремо базову послугу для відображення
      const serv = group.baseService;
      let rawDescription = serv.description || "";
      let saleMatch = rawDescription.match(/\bSALE(\d+(?:\.\d{1,2})?)\b/i);
      if (saleMatch) {
        rawDescription = rawDescription.replace(/\s*\bSALE\d+(?:\.\d{1,2})?\b\s*/i, " ").trim();
      }

      // Якщо є варіанти - показуємо базову назву, інакше - повну назву послуги
      let serviceName = group.hasVariants ? group.baseName : extractTextByLang(serv.name, window.appLang);
      let serviceDescription = extractTextByLangDescription(rawDescription, window.appLang);

      let oldPriceHtml = "";
      if (saleMatch) {
        const oldPrice = parseFloat(saleMatch[1]);
        if (!isNaN(oldPrice)) {
          oldPriceHtml = `<span class="service-price old-price">${oldPrice} ${_currencySymbol()}</span>`;
        }
      }

      const hideDuration = shouldHideDurationForService(serv);
      const durationHtml = hideDuration
          ? ""
          : `<span class="service-duration">${serv.duration} ${window.translations["indDurationService"]}</span>`;

      // Якщо є варіанти з різними рівнями - показуємо "Від X"
      // "Від" prefix тільки якщо ціни різні (range), інакше — просто число.
      const pricePrefix = (group.hasVariants && group.maxPrice > group.minPrice)
        ? (window.translations["priceFrom"] || "From") + " "
        : "";
      const priceHtml = `<span class="service-price">${pricePrefix}${group.minPrice} ${_currencySymbol()}</span>`;

      // Зберігаємо всі варіанти як JSON для передачі в dataappointments.js
      const variantsJson = encodeURIComponent(JSON.stringify(group.variants.map(v => ({
        id: v.id,
        name: v.name,
        duration: v.duration,
        location_prices: v.location_prices,
        price_currency: v.price_currency,
        description: v.description,
        location_position: v.location_position,
        category: v.category,
        level: v.level,
        levelLabel: v.levelLabel
      }))));

      html += `
        <div class="service-row"
             data-location-position="${serv.location_position}"
             data-category="${serv.category}"
             data-has-variants="${group.hasVariants}"
             data-variants="${variantsJson}">
          <div class="service-header">
            <div class="accordion-toggle"><img src="${arrowdown}" alt="Toggle Accordion"></div>
            <div class="service-name_duration">
              <span class="service-name">${serviceName}</span>
              ${durationHtml}
            </div>
            <div class="priceAndBtnAdd">
              ${oldPriceHtml}${priceHtml}
              <button class="service-add"
                data-id="${serv.id}"
                data-service-name="${serviceName}"
                data-duration="${serv.duration}"
                data-location_prices="${serv.location_prices}"
                data-price_currency="${serv.price_currency}"
                data-description="${rawDescription}"
                data-location-position="${serv.location_position}"
                data-category="${serv.category}"
                data-has-variants="${group.hasVariants}"
                data-variants="${variantsJson}"><img class="plus" src="${plus}"></button>
            </div>
          </div>
          <div class="service-description" style="display: none;">
            ${serviceDescription}
          </div>
        </div>
      `;
    });
    html += `</div>`;
  }

  if (category.children && category.children.length > 0) {
    html += `<div class="subcategories">`;
    category.children.forEach(subcat => {
      html += renderSubcategoryHTML(subcat);
    });
    html += `</div>`;
  }

  document.getElementById("servicesList").innerHTML = html;
  initAccordionToggles();
}

/***************************************************
 * 10) Функция обновления перечня услуг при изменении выбора в dropdown
 **************************************************/
function updateServicesList() {
  renderServices(window.currentCategory);
}

/***************************************************
 * 11) Новый обработчик после выбора длины волос
 **************************************************/
function handleHairLengthSelection() {
  const selectorDiv = document.getElementById("hairLengthSelector");
  if (selectorDiv) {
    selectorDiv.style.display = "none";
  }
  const dropdownContainer = document.getElementById("hairLengthDropdownContainer");
  if (dropdownContainer) {
    dropdownContainer.style.display = "block";
    dropdownContainer.innerHTML = generateHairLengthDropdownHTML();
    const dropdown = dropdownContainer.querySelector("select");
    if (dropdown) {
      dropdown.addEventListener("change", function() {
        selectedHairLength = this.value;
        updateServicesList();
      });
    }
  }
  const servicesDiv = document.getElementById("servicesList");
  if (servicesDiv) {
    servicesDiv.style.display = "block";
    renderServices(window.currentCategory);
  }
}

/***************************************************
 * 12) Генерация HTML для выпадающего списка длины волос
 **************************************************/
// Генерация HTML для выпадающего списка выбора длины волос с учетом языка
function generateHairLengthDropdownHTML() {
  const currentLang = window.appLang ? window.appLang.toLowerCase() : 'en';
  let html = `<select id="hairLengthDropdown">`;
  HAIR_LENGTH_OPTIONS.forEach(opt => {
    // Для display беремо перший суфікс (descriptive), бо array
    const suf = _sufArr(opt, currentLang)[0] || '';
    html += `<option value="${opt.id}" ${opt.id === selectedHairLength ? "selected" : ""}>
      ${_hairRange(opt)} ${suf}
    </option>`;
  });
  html += `</select>`;
  return html;
}


/***************************************************
 * 13) Обновление левого списка категорий
 **************************************************/
async function updateCategoryList() {
  filteredCategories = filterCategoriesNoAddon(window.categoriesData);
  const initialCat = findCategoryByParam(initialCategoryParam, filteredCategories);
  const listDiv = document.querySelector(".leftSide #category-list");
  if (listDiv) {
    let leftHtml = `<p class="category-title">${window.translations["categoryTitle"]}</p>`;
    filteredCategories.forEach(cat => {
      // Применяем extractTextByLang к названию категории
      leftHtml += `<div class="categoryItem" data-cat-id="${cat.id}" onclick="selectCategory('${cat.id}')">${extractTextByLang(cat.name, window.appLang)}</div>`;
    });
    listDiv.innerHTML = leftHtml;
  }

  const mobileDropdown = document.getElementById("mobileCategoryDropdown");
  if (mobileDropdown) {
    let selectHtml = `<select id="mobileCategorySelect" onchange="selectCategory(this.value)">`;
    selectHtml += `<option value="" disabled ${initialCat ? '' : 'selected'}>${translations["serviceCategoryPhone"]}</option>`;
    filteredCategories.forEach(cat => {
      const selectedAttr = initialCat && initialCat.id === cat.id ? 'selected' : '';
      selectHtml += `<option value="${cat.id}" ${selectedAttr}>${extractTextByLang(cat.name, window.appLang)}</option>`;
    });
    selectHtml += `</select>`;
    mobileDropdown.innerHTML = selectHtml;
  }

  resetMiddleSide();

   if (initialCat) {
    await selectCategory(initialCat.id);
  } else if (filteredCategories.length > 0) {
    await selectCategory(filteredCategories[0].id);
  } else {
    const servicesDiv = document.getElementById("servicesList");
    if (servicesDiv) {
      servicesDiv.innerHTML = `<p>${window.translations["categoryNotFound"]}</p>`;
    }
  }
}

/***************************************************
 * 14) Выбор категории (при клике слева)
 **************************************************/
async function selectCategory(catId) {
  resetMiddleSide();

  const categoryItems = document.querySelectorAll(".categoryItem");
  categoryItems.forEach(item => {
    item.classList.toggle("active", item.getAttribute("data-cat-id") === catId);
  });

  let cat = findCategoryById(catId, filteredCategories);
  if (!cat) return;

  let overlayDisplayed = false;
  try {
    if (!cat.__servicesLoaded) {
      showLoadingOverlay();
      overlayDisplayed = true;
    }

    cat = await ensureCategoryServices(catId);
    if (!cat) {
      throw new Error('Категория не найдена после загрузки');
    }

    renderAndShowCategory(cat);
    const url = new URL(window.location);
    url.searchParams.set('category', extractTextByLang(cat.name, window.appLang));
    window.history.replaceState({}, '', url);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) {
    console.error('Не удалось загрузить услуги категории', error);
    const servicesDiv = document.getElementById('servicesList');
    if (servicesDiv) {
      servicesDiv.innerHTML = `<p>${window.translations["categoryLoadError"] || 'Unable to load services right now.'}</p>`;
    }
  } finally {
    if (overlayDisplayed) {
      hideLoadingOverlay();
    }
  }
}

/***************************************************
 * 15) Поиск категории рекурсивно
 **************************************************/
function findCategoryById(catId, list) {
  for (let i = 0; i < list.length; i++) {
    if (list[i].id === catId) return list[i];
    if (list[i].children && list[i].children.length > 0) {
      const found = findCategoryById(catId, list[i].children);
      if (found) return found;
    }
  }
  return null;
}
// Поиск категории по параметру (id или локализованное название)
function findCategoryByParam(param, list) {
  if (!param) return null;
  param = param.toLowerCase();
  for (let i = 0; i < list.length; i++) {
    const cat = list[i];
    if (cat.id.toLowerCase() === param) return cat;
    const localized = extractTextByLang(cat.name, window.appLang).toLowerCase();
    if (localized === param) return cat;
    if (cat.children && cat.children.length > 0) {
      const found = findCategoryByParam(param, cat.children);
      if (found) return found;
    }
  }
  return null;
}

/***************************************************
 * 16) Инициализация при загрузке страницы
 * Вибір рівня майстра видалено - рівень тепер визначається автоматично при виборі часу
 **************************************************/
window.onload = async function() {
  showLoadingOverlay();
  try {
    const salonCode = window.salon || 'l1';
    await loadCategoriesForSalon(salonCode);
    await updateCategoryList();
  } catch (error) {
    console.error('Ошибка инициализации категорий', error);
    const servicesDiv = document.getElementById('servicesList');
    if (servicesDiv) {
      servicesDiv.innerHTML = `<p>${window.translations["categoryLoadError"] || 'Unable to load services right now.'}</p>`;
    }
  } finally {
    hideLoadingOverlay();
  }
};

/*** Salon picker: home view = salons of current city, плюс інші міста / країни ***/
(function initSalonPicker() {
  const hierarchy = window.salonsHierarchy || [];
  const countryNames = window.salonCountryNames || {};
  const picker = document.getElementById('salonPicker');
  const button = document.getElementById('salonPickerButton');
  const panel = document.getElementById('salonPickerPanel');
  const list = document.getElementById('salonPickerList');
  const header = document.getElementById('salonPickerBack');
  const crumbs = document.getElementById('salonPickerCrumbs');
  const label = document.getElementById('salonPickerLabel');
  const backBtn = header && header.querySelector('.salon-picker-back-btn');
  if (!picker || !button || !panel || !list || !hierarchy.length) return;

  const current = window.salon || 'l1';

  // знайти country/city/salon для current
  let initCountry = null, initCity = null, initSalon = null;
  for (const c of hierarchy) {
    for (const city of c.cities) {
      const s = city.salons.find(x => x.code === current);
      if (s) { initCountry = c; initCity = city; initSalon = s; break; }
    }
    if (initSalon) break;
  }
  if (!initSalon) {
    initCountry = hierarchy[0];
    initCity = hierarchy[0].cities[0];
    initSalon = hierarchy[0].cities[0].salons[0];
  }

  // Label — тільки назва салону
  label.textContent = initSalon.name;

  // state machine:
  //   'home'           — salons поточного міста + (інші міста цієї країни) + (інші країни)
  //   'city_salons'    — список салонів іншого вибраного міста
  //   'country_cities' — список міст іншої вибраної країни
  let level = 'home';
  let viewCountry = null;
  let viewCity = null;

  function gotoSalon(code) {
    if (code === current) { closePanel(); return; }
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.style.display = 'flex';
    const lang = (window.appLang || 'UA').toLowerCase();
    window.location.href = `/${code}/${lang}`;
  }

  function makeSection(titleText) {
    const li = document.createElement('li');
    li.className = 'salon-picker-section';
    li.textContent = titleText;
    return li;
  }

  function makeItem(text, opts) {
    opts = opts || {};
    const li = document.createElement('li');
    li.className = 'salon-picker-item';
    if (opts.current) li.classList.add('is-current');
    li.textContent = text;
    if (opts.hasChildren) {
      li.classList.add('has-children');
      const arrow = document.createElement('span');
      arrow.className = 'salon-picker-item-arrow';
      arrow.textContent = '›';
      li.appendChild(arrow);
    }
    if (opts.onClick) {
      li.addEventListener('click', e => {
        // Зупиняємо bubbling, інакше document click handler побачить що
        // e.target вже видалено (replaceChildren) і закриє панель.
        e.stopPropagation();
        opts.onClick(e);
      });
    }
    return li;
  }

  function renderList() {
    list.replaceChildren();
    if (level === 'home') {
      header.style.display = 'none';
      // Salons of current city
      list.appendChild(makeSection(initCity.city));
      initCity.salons.forEach(salon => {
        list.appendChild(makeItem(salon.name, {
          current: salon.code === current,
          onClick: () => gotoSalon(salon.code),
        }));
      });
      // Other cities in current country
      const otherCities = initCountry.cities.filter(c => c.city !== initCity.city);
      const t = window.translations || {};
      if (otherCities.length) {
        list.appendChild(makeSection(t.otherCities || 'Other cities'));
        otherCities.forEach(city => {
          list.appendChild(makeItem(city.city, {
            hasChildren: true,
            onClick: () => {
              viewCountry = initCountry;
              viewCity = city;
              level = 'city_salons';
              renderList();
            },
          }));
        });
      }
      // Other countries
      const otherCountries = hierarchy.filter(c => c.country !== initCountry.country);
      if (otherCountries.length) {
        list.appendChild(makeSection(t.otherCountries || 'Other countries'));
        otherCountries.forEach(country => {
          const cn = countryNames[country.country] || country.country;
          list.appendChild(makeItem(cn, {
            hasChildren: true,
            onClick: () => {
              viewCountry = country;
              // якщо одне місто — одразу до салонів
              if (country.cities.length === 1) {
                viewCity = country.cities[0];
                level = 'city_salons';
              } else {
                viewCity = null;
                level = 'country_cities';
              }
              renderList();
            },
          }));
        });
      }
    } else if (level === 'country_cities') {
      header.style.display = 'flex';
      crumbs.textContent = countryNames[viewCountry.country] || viewCountry.country;
      viewCountry.cities.forEach(city => {
        list.appendChild(makeItem(city.city, {
          hasChildren: true,
          onClick: () => {
            viewCity = city;
            level = 'city_salons';
            renderList();
          },
        }));
      });
    } else if (level === 'city_salons') {
      header.style.display = 'flex';
      const cn = countryNames[viewCountry.country] || viewCountry.country;
      crumbs.textContent = (viewCountry.country === initCountry.country)
        ? viewCity.city
        : `${cn} / ${viewCity.city}`;
      viewCity.salons.forEach(salon => {
        list.appendChild(makeItem(salon.name, {
          current: salon.code === current,
          onClick: () => gotoSalon(salon.code),
        }));
      });
    }
  }

  function openPanel() {
    level = 'home';
    viewCountry = null;
    viewCity = null;
    renderList();
    panel.classList.add('is-open');
    panel.setAttribute('aria-hidden', 'false');
    button.setAttribute('aria-expanded', 'true');
  }
  function closePanel() {
    panel.classList.remove('is-open');
    panel.setAttribute('aria-hidden', 'true');
    button.setAttribute('aria-expanded', 'false');
  }

  button.addEventListener('click', e => {
    e.stopPropagation();
    if (panel.classList.contains('is-open')) closePanel();
    else openPanel();
  });
  if (backBtn) {
    backBtn.addEventListener('click', e => {
      e.stopPropagation();
      if (level === 'city_salons') {
        // якщо ми потрапили сюди з country_cities — повертаємось туди;
        // якщо з home (тобто viewCountry === initCountry АБО інша країна з 1 містом) — повертаємось home
        if (viewCountry && viewCountry.country !== initCountry.country && viewCountry.cities.length > 1) {
          level = 'country_cities';
          viewCity = null;
        } else {
          level = 'home';
          viewCountry = null;
          viewCity = null;
        }
      } else if (level === 'country_cities') {
        level = 'home';
        viewCountry = null;
      }
      renderList();
    });
  }
  // Закриваємо лише якщо клік потрапив поза picker AND панель відкрита.
  // mousedown спрацьовує до click, але e.target ще валідний — використовуємо click
  // з захистом: ігноруємо якщо target вже не в DOM (значить був у нашому списку).
  document.addEventListener('click', e => {
    if (e.target && !e.target.isConnected) return;
    if (!picker.contains(e.target)) closePanel();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closePanel();
  });
})();