-- Migration: Create locations and salons tables
-- Replaces hardcoded LOCATIONS, SALON_DIRS, SALON_DATABASE_CODES

CREATE TABLE IF NOT EXISTS locations (
    slug VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    country VARCHAR(50) NOT NULL,
    country_code VARCHAR(5) NOT NULL DEFAULT '',
    image VARCHAR(255) DEFAULT NULL,
    status ENUM('active', 'planned', 'inactive') DEFAULT 'planned',
    sort_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS salons (
    code VARCHAR(20) PRIMARY KEY,
    location_slug VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL COMMENT 'Display name, e.g. Oxford Circus',
    address_line VARCHAR(255) DEFAULT NULL,
    postal_code VARCHAR(50) DEFAULT NULL,
    phone_display VARCHAR(50) DEFAULT NULL,
    phone_link VARCHAR(50) DEFAULT NULL,
    email VARCHAR(100) DEFAULT NULL,
    database_code VARCHAR(20) DEFAULT NULL COMMENT 'AIHelps CRM database code',
    payment_location_id VARCHAR(20) DEFAULT NULL COMMENT 'EasyTip payment location ID',
    data_dir VARCHAR(255) NOT NULL COMMENT 'Folder name under static/data/salons/',
    image VARCHAR(255) DEFAULT NULL,
    area_icon VARCHAR(255) DEFAULT NULL,
    map_embed_url TEXT DEFAULT NULL,
    map_link TEXT DEFAULT NULL,
    telegram_token VARCHAR(255) DEFAULT NULL,
    telegram_chat_id VARCHAR(100) DEFAULT NULL,
    languages JSON DEFAULT NULL COMMENT '["en","ua","ru"]',
    menu_links JSON DEFAULT NULL,
    status ENUM('active', 'planned', 'inactive') DEFAULT 'planned',
    sort_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (location_slug) REFERENCES locations(slug)
);

-- ═══ SEED DATA ═══

-- Locations
INSERT INTO locations (slug, name, country, country_code, status, sort_order) VALUES
('london',    'Лондон',     'Великобританія', 'uk', 'active',  1),
('kyiv',      'Київ',       'Україна',        'ua', 'planned', 2),
('bucha',     'Буча',       'Україна',        'ua', 'planned', 3),
('kharkiv',   'Харків',     'Україна',        'ua', 'planned', 4),
('odesa',     'Одеса',      'Україна',        'ua', 'planned', 5),
('volodymyr', 'Володимир',  'Україна',        'ua', 'planned', 6),
('uzhhorod',  'Ужгород',    'Україна',        'ua', 'planned', 7),
('warsaw',    'Варшава',    'Польща',          'pl', 'planned', 8),
('wroclaw',   'Вроцлав',    'Польща',          'pl', 'planned', 9);

-- Salons
INSERT INTO salons (code, location_slug, name, address_line, postal_code, phone_display, phone_link, email, database_code, payment_location_id, data_dir, image, area_icon, telegram_token, telegram_chat_id, languages, status, sort_order) VALUES
-- London
('l1', 'london', 'Mortimer Street',    '67, Mortimer Street, London',    'W1W 7SE',  '07907707767',      '447907707767',   'office@p-de-p.co.uk', '776611', '3194', 'london-mortimer',  'img/london1.jpg', 'img/areaLondon.png', '8097258835:AAGeiKBHjARK4ihpvTWeXPsDDy063fR5O9I', '-1001961563167', '["en","ua","ru"]', 'active', 1),
('l2', 'london', 'Brompton Road',      '62 Old Brompton Road, London',   'SW7 3LQ',  '+44 07775 449715', '4407775449715',  'office@p-de-p.co.uk', '703835', '3396', 'london-brompton',  'img/london2.jpg', 'img/areaLondon.png', '8097258835:AAGeiKBHjARK4ihpvTWeXPsDDy063fR5O9I', '-1002646353157', '["en","ua","ru"]', 'active', 2),

-- Kyiv
('k1', 'kyiv', 'Бессарабка',           'Бессарабська пл., 7',           NULL, NULL, NULL, NULL, NULL, NULL, 'kyiv-bessarabka',    NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),
('k2', 'kyiv', 'БЦ Лео',              'вул. Б. Хмельницького, 19/21',  NULL, NULL, NULL, NULL, NULL, NULL, 'kyiv-khmelnytsky',   NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 2),
('k3', 'kyiv', 'Позняки',              'вул. А. Ахматової, 44/11',      NULL, NULL, NULL, NULL, NULL, NULL, 'kyiv-poznyaky',      NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 3),
('k4', 'kyiv', 'Золоті Ворота',        'вул. Ярославів Вал, 19',        NULL, NULL, NULL, NULL, NULL, NULL, 'kyiv-golden-gates',  NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 4),
('k5', 'kyiv', 'Оболонь',              'просп. Володимира Івасюка, 2ГК2', NULL, NULL, NULL, NULL, NULL, NULL, 'kyiv-obolon',       NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 5),

-- Bucha
('bu1', 'bucha', 'Avenir Plaza',        'бул. Леоніда Бірюкова, 2',     NULL, NULL, NULL, NULL, NULL, NULL, 'bucha-avenir',       NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),

-- Kharkiv
('kh1', 'kharkiv', 'Наукова',           'просп. Науки, 9',              NULL, NULL, NULL, NULL, NULL, NULL, 'kharkiv-nauki',      NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),
('kh2', 'kharkiv', 'Площа Конституції', 'просп. Героїв Харкова, 15',    NULL, NULL, NULL, NULL, NULL, NULL, 'kharkiv-heroiv',     NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 2),

-- Volodymyr
('vl1', 'volodymyr', 'Центр',           'вул. К. Василька, 6а',         NULL, NULL, NULL, NULL, NULL, NULL, 'volodymyr-center',   NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),

-- Odesa
('od1', 'odesa', 'Караванського',       'вул. Святослава Караванського, 23', NULL, NULL, NULL, NULL, NULL, NULL, 'odesa-karavanskoho', NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),
('od2', 'odesa', 'Морський комплекс',   'вул. Львівська, 15Б',          NULL, NULL, NULL, NULL, NULL, NULL, 'odesa-lvivska',      NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 2),

-- Uzhhorod
('uz1', 'uzhhorod', 'Dream City',       'вул. Гойди, 10а',              NULL, NULL, NULL, NULL, NULL, NULL, 'uzhhorod-dream-city', NULL, NULL, NULL, NULL, '["ua","en","ru"]', 'planned', 1),

-- Warsaw
('wa1', 'warsaw', 'Piekna',             'вул. Piekna, 49',              NULL, NULL, NULL, NULL, NULL, NULL, 'warsaw-piekna',      NULL, NULL, NULL, NULL, '["pl","ua","en"]', 'planned', 1),
('wa2', 'warsaw', 'Grzybowska',         'вул. Grzybowska, 4',           NULL, NULL, NULL, NULL, NULL, NULL, 'warsaw-grzybowska',  NULL, NULL, NULL, NULL, '["pl","ua","en"]', 'planned', 2),
('wa3', 'warsaw', 'Wilanów',            'Królewska Wieś, 18',           NULL, NULL, NULL, NULL, NULL, NULL, 'warsaw-wilanow',     NULL, NULL, NULL, NULL, '["pl","ua","en"]', 'planned', 3),

-- Wroclaw
('wr1', 'wroclaw', 'Kosciuszko',        'пл. Tadeusza Kosciuszki, 13/1a', NULL, NULL, NULL, NULL, NULL, NULL, 'wroclaw-kosciuszko', NULL, NULL, NULL, NULL, '["pl","ua","en"]', 'planned', 1);
