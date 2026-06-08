from flask import Flask, render_template, request, jsonify, redirect, url_for, g
import json
import os
import copy
from datetime import datetime, timedelta
import uuid
from dotenv import load_dotenv

from _db import get_db as _get_db

# Завантажуємо змінні з .env файлу
load_dotenv()
from admin import admin_bp, init_admin_tables, start_service_update_worker
from appointmentService import (
    create_appointment_service,
    save_appointment_record,
    create_payment_link,
    send_to_telegram,
    monitor_payment_status,
    render_service_email_html,
    send_to_email,
)
from mailchimp_service import add_to_mailchimp
from freetime import update_free_time

app = Flask(__name__)
app.config['CACHE_VERSION'] = '3.2'


@app.url_defaults
def _static_cache_buster(endpoint, values):
    """Auto-append ?v=<mtime> до всіх url_for('static', ...).

    Версія міняється коли файл реально змінюється — браузер автоматично тягне
    свіжу копію, не треба ручного бампа CACHE_VERSION."""
    if endpoint == 'static' and 'v' not in values:
        filename = values.get('filename')
        if filename:
            try:
                full = os.path.join(app.static_folder, filename)
                values['v'] = int(os.path.getmtime(full))
            except OSError:
                values['v'] = app.config.get('CACHE_VERSION', '1')
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'pdp_secret_key')

# Реєструємо адмін-панель
app.register_blueprint(admin_bp)

# Ініціалізуємо таблицю адмін-користувачів при старті (в фоні)
import threading as _threading
_threading.Thread(target=init_admin_tables, daemon=True).start()

# Запуск фонового воркера для повторних спроб оновлення послуг в CRM
start_service_update_worker()

# Context processor щоб CACHE_VERSION був доступний у всіх шаблонах
@app.context_processor
def inject_cache_version():
    return {'CACHE_VERSION': app.config['CACHE_VERSION']}

# Функції для роботи з резервуваннями (гібридний підхід: in-memory + async MySQL)
import threading

# In-memory кеш резервувань (швидкий доступ)
RESERVATIONS = {}
_reservations_lock = threading.Lock()

def get_db_connection():
    """Postgres booking-schema connection."""
    return _get_db()

def _init_reservations_table_async():
    """Завантажує активні резервування з БД у кеш. DDL уже накатано міграцією."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT employee_id, reservation_date, reservation_time, visit_id, expires_at FROM slot_reservations WHERE expires_at > NOW()")
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        with _reservations_lock:
            for row in rows:
                key = (row[0], row[1], row[2])
                RESERVATIONS[key] = {
                    'visit_id': row[3],
                    'expires_at': row[4]
                }
        print(f"Loaded {len(rows)} reservations from DB")
    except Exception as e:
        print(f"Error initializing reservations: {e}")

def _save_reservation_to_db(employee_id, date, time, visit_id, expires_at):
    """Зберегти резервування в БД асинхронно."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO slot_reservations (employee_id, reservation_date, reservation_time, visit_id, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (employee_id, reservation_date, reservation_time) DO UPDATE
                SET visit_id = EXCLUDED.visit_id,
                    expires_at = EXCLUDED.expires_at
        """, (employee_id, date, time, visit_id, expires_at))
        connection.commit()
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"Error saving reservation to DB: {e}")

def _delete_reservation_from_db(employee_id, date, time, visit_id):
    """Видалити резервування з БД асинхронно"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            DELETE FROM slot_reservations
            WHERE employee_id = %s AND reservation_date = %s AND reservation_time = %s AND visit_id = %s
        """, (employee_id, date, time, visit_id))
        connection.commit()
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"Error deleting reservation from DB: {e}")

def _cleanup_expired():
    """Очистити прострочені резервування з кешу"""
    now = datetime.utcnow()
    with _reservations_lock:
        expired = [k for k, v in RESERVATIONS.items() if v['expires_at'] <= now]
        for k in expired:
            RESERVATIONS.pop(k, None)

def add_reservation(employee_id, date, time, visit_id):
    """Додати резервування (миттєво в пам'ять, асинхронно в БД)"""
    _cleanup_expired()
    key = (employee_id, date, time)

    with _reservations_lock:
        if key in RESERVATIONS:
            return False
        expires_at = datetime.utcnow() + timedelta(minutes=5)
        RESERVATIONS[key] = {
            'visit_id': visit_id,
            'expires_at': expires_at
        }

    # Зберігаємо в БД асинхронно (не блокуємо відповідь)
    threading.Thread(
        target=_save_reservation_to_db,
        args=(employee_id, date, time, visit_id, expires_at),
        daemon=True
    ).start()

    return True

def remove_reservation(employee_id, date, time, visit_id):
    """Видалити резервування (миттєво з пам'яті, асинхронно з БД)"""
    _cleanup_expired()
    key = (employee_id, date, time)

    with _reservations_lock:
        info = RESERVATIONS.get(key)
        if info and info.get('visit_id') == visit_id:
            RESERVATIONS.pop(key, None)

    # Видаляємо з БД асинхронно
    threading.Thread(
        target=_delete_reservation_from_db,
        args=(employee_id, date, time, visit_id),
        daemon=True
    ).start()

def get_all_reservations():
    """Отримати всі активні резервування (миттєво з пам'яті + оплачені записи що очікують CRM)"""
    _cleanup_expired()
    result = {}

    # 1. Існуючі short-term резервування з in-memory кешу
    with _reservations_lock:
        for (emp, d, t), info in RESERVATIONS.items():
            result.setdefault(emp, {}).setdefault(d, []).append(t)

    # 2. Слоти з оплачених записів, що ще не створені в CRM
    # Це блокує слоти поки CRM API недоступний
    try:
        from crm_retry_queue import get_pending_appointment_slots
        pending = get_pending_appointment_slots()
        for emp, dates in pending.items():
            for d, times in dates.items():
                for t in times:
                    if t not in result.get(emp, {}).get(d, []):
                        result.setdefault(emp, {}).setdefault(d, []).append(t)
    except Exception as e:
        print(f"Error getting pending appointment slots: {e}")

    return result

# Ініціалізуємо таблицю резервувань в фоновому потоці (не блокує старт)
threading.Thread(target=_init_reservations_table_async, daemon=True).start()

# Відновлюємо записи, що застрягли в стані 'processing' після перезапуску сервера
from crm_retry_queue import recover_stale_processing
threading.Thread(target=recover_stale_processing, daemon=True).start()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'static', 'data')

TRANSLATION_DIR = os.path.join(BASE_DIR, 'static', 'data', 'translations')

SALONS_DATA_ROOT = os.path.join('static', 'data', 'salons')

# ── DB-driven salon/location lookups (cached with 60s TTL) ──
import time as _time
_salon_cache = {'salons': {}, 'dirs': {}, 'info_all': {}, 'location_langs': {}, 'loaded': False, 'ts': 0}
_CACHE_TTL = 60  # seconds

# Per-country default language switcher. Override DB значень — щоб уникнути
# drift у адмінці (UA салон ніколи не покаже PL chip навіть якщо хтось
# випадково додав). User-вимога:
#   GB → лишаємо як є (DB) — пустий override
#   UA → ua + en
#   PL → pl + ua + ru + en
_COUNTRY_DEFAULT_LANGS = {
    'ua': ['ua', 'en'],
    'pl': ['pl', 'ua', 'ru', 'en'],
}


def _load_salon_cache():
    """Load all salons + location languages from DB into memory cache."""
    now = _time.time()
    if _salon_cache['loaded'] and (now - _salon_cache['ts']) < _CACHE_TTL:
        return
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)

        # Load location languages first. Country-based defaults override DB,
        # щоб адмінка-drift не зламала switcher (UA не показує PL, etc).
        cursor.execute("SELECT slug, country_code, languages FROM locations")
        for loc_row in cursor.fetchall():
            cc = (loc_row.get('country_code') or '').lower()
            default = _COUNTRY_DEFAULT_LANGS.get(cc)
            if default:
                _salon_cache['location_langs'][loc_row['slug']] = list(default)
                continue
            val = loc_row.get('languages')
            if val and isinstance(val, str):
                val = json.loads(val)
            elif val and isinstance(val, (bytes, bytearray)):
                val = json.loads(val.decode('utf-8'))
            _salon_cache['location_langs'][loc_row['slug']] = val or ['en']

        cursor.execute("SELECT * FROM salons WHERE status IN ('active','planned') ORDER BY sort_order")
        rows = cursor.fetchall()
        cursor.close()
        connection.close()
        for row in rows:
            code = row['code']
            # Parse JSON fields
            for field in ('languages', 'menu_links'):
                val = row.get(field)
                if val and isinstance(val, str):
                    row[field] = json.loads(val)
                elif val and isinstance(val, (bytes, bytearray)):
                    row[field] = json.loads(val.decode('utf-8'))
            # Override salon languages with location languages
            loc_slug = row.get('location_slug', '')
            row['languages'] = _salon_cache['location_langs'].get(loc_slug, ['en'])
            _salon_cache['salons'][code] = row
            _salon_cache['dirs'][code] = os.path.join(SALONS_DATA_ROOT, row['data_dir'])
            # Build data-info compatible dict for templates
            _salon_cache['info_all'][code] = _salon_row_to_info(row)
        _salon_cache['loaded'] = True
        _salon_cache['ts'] = _time.time()
        print(f"[Salon Cache] Loaded {len(rows)} salons from DB")
    except Exception as e:
        print(f"[Salon Cache] Error loading from DB: {e}")


def _salon_row_to_info(row):
    """Convert DB salon row to data-info.json compatible dict for templates."""
    return {
        'id': str(row.get('id') or ''),
        'country': (row.get('country') or '').lower(),
        'nameLocation': row.get('name', ''),
        'name': row.get('address_line', ''),
        'codeLocation': (row.get('postal_code') or '') + (', London' if 'london' in (row.get('location_slug') or '') else ''),
        'database_code': row.get('database_code', ''),
        'payment_location_id': row.get('payment_location_id', ''),
        'image': row.get('image', ''),
        'sidebar_image': row.get('image', ''),
        'areaIcon': row.get('area_icon', ''),
        'map_embed_url': row.get('map_embed_url', ''),
        'map_link': row.get('map_link', ''),
        'telegram_token': row.get('telegram_token', ''),
        'telegram_chat_id': row.get('telegram_chat_id', ''),
        'whatsapp_phone': row.get('whatsapp_phone') or '',
        'instagram_url': row.get('instagram_url') or '',
        'hours_label': row.get('hours_label') or '',
        'hours_value': row.get('hours_value') or '',
        'smtp_from_email': row.get('smtp_from_email') or '',
        'data_dir': row.get('data_dir') or '',
        'payment_enabled': bool(row.get('payment_enabled')),
        'ai_chat_enabled': bool(row.get('ai_chat_enabled', True)),
        'address': {
            'line': row.get('address_line', ''),
            'phone_display': row.get('phone_display', ''),
            'phone_link': row.get('phone_link', ''),
            'email': row.get('email', ''),
        },
        'menu_links': row.get('menu_links') or {},
        'languages': row.get('languages') or ['en'],
    }


def reload_salon_cache():
    """Force reload salon cache (call after DB changes)."""
    _salon_cache['salons'].clear()
    _salon_cache['dirs'].clear()
    _salon_cache['info_all'].clear()
    _salon_cache['location_langs'].clear()
    _salon_cache['loaded'] = False
    _salon_cache['ts'] = 0
    _load_salon_cache()


def get_salon_dirs():
    """Get {code: folder_path} dict — replaces old SALON_DIRS."""
    _load_salon_cache()
    return _salon_cache['dirs']


def get_salon_info_all():
    """Get {code: info_dict} — replaces old SALON_INFO_ALL."""
    _load_salon_cache()
    return _salon_cache['info_all']


# Country code (booking.salons.country) -> translation key in static/data/translations/*.json
_COUNTRY_TRANSLATION_KEY = {'gb': 'countryUK', 'ua': 'countryUA', 'pl': 'countryPL'}
# Sort order for country dropdown (locale-friendly, deterministic)
_COUNTRY_ORDER = {'gb': 0, 'ua': 1, 'pl': 2}


def get_salons_hierarchy():
    """Повертає список:
        [{country: 'gb', country_key: 'countryUK',
          cities: [{city: 'London',
                    salons: [{code: 'l1', name: 'Oxford Circus', address: '...'}, ...]}]
         }, ...]

    Зручно для каскадного UI (країна → місто → салон) на /<salon>/<lang>.
    """
    _load_salon_cache()
    by_country: dict[str, dict] = {}
    for code, row in _salon_cache['salons'].items():
        ccode = (row.get('country') or '').lower()
        if ccode not in _COUNTRY_TRANSLATION_KEY:
            continue
        country = by_country.setdefault(ccode, {
            'country': ccode,
            'country_key': _COUNTRY_TRANSLATION_KEY[ccode],
            'cities': {},
        })
        city_name = row.get('city') or ''
        city = country['cities'].setdefault(city_name, {'city': city_name, 'salons': []})
        city['salons'].append({
            'code': code,
            'name': row.get('name', ''),
            'address': row.get('address_line') or '',
        })

    # finalize: cities → sorted list, salons → sorted by sort_order
    out = []
    for ccode in sorted(by_country, key=lambda c: _COUNTRY_ORDER.get(c, 99)):
        country = by_country[ccode]
        cities = []
        for city_name in sorted(country['cities']):
            city = country['cities'][city_name]
            city['salons'].sort(key=lambda s: _salon_cache['salons'].get(s['code'], {}).get('sort_order', 0))
            cities.append(city)
        country['cities'] = cities
        out.append(country)
    return out


def load_salon_info(salon):
    """Load info for a single salon."""
    info = get_salon_info_all().get(salon)
    if not info:
        raise ValueError(f'Unknown salon: {salon}')
    return info


def get_salon_row(salon_code):
    """Get raw DB row for a salon (with payment_enabled etc.)."""
    _load_salon_cache()
    return _salon_cache['salons'].get(salon_code)


SALON_CATALOG_CACHE = {}

@app.before_request
def ensure_visit_cookie():
    if not request.cookies.get('visit_id'):
        g.new_visit_id = str(uuid.uuid4())
    else:
        g.new_visit_id = None

@app.after_request
def add_visit_cookie(response):
    if getattr(g, 'new_visit_id', None):
        response.set_cookie('visit_id', g.new_visit_id, max_age=60*60*24*30)
    # Prevent browser from caching HTML pages — щоб після deploy клієнти НЕ
    # бачили старий HTML (особливо bfcache, proxy, aggressive browser cache).
    # Усі три заголовки разом — модерн + legacy (HTTP/1.0 Pragma, Expires).
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

def load_json(filename):
    with open(os.path.join(DATA_DIR, filename), encoding='utf-8') as f:
        return json.load(f)

def load_salon_json(salon, filename):
    folder = get_salon_dirs().get(salon)
    if not folder:
        raise ValueError('Unknown salon')
    path = os.path.join(BASE_DIR, folder, filename)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def get_salon_catalog(salon):
    folder = get_salon_dirs().get(salon)
    if not folder:
        raise ValueError('Unknown salon')

    categories_path = os.path.join(BASE_DIR, folder, 'categories.json')
    services_path = os.path.join(BASE_DIR, folder, 'services.json')
    mtimes = (
        os.path.getmtime(categories_path),
        os.path.getmtime(services_path)
    )

    cached = SALON_CATALOG_CACHE.get(salon)
    if cached and cached.get('mtimes') == mtimes:
        return cached

    categories = load_salon_json(salon, 'categories.json')
    services = load_salon_json(salon, 'services.json')

    category_tree = build_category_tree(copy.deepcopy(categories))
    services_map = assign_services_to_categories(category_tree, services)

    category_index = {}

    def index_categories(nodes):
        for node in nodes:
            category_index[node['id']] = node
            if node.get('children'):
                index_categories(node['children'])

    index_categories(category_tree)

    catalog = {
        'mtimes': mtimes,
        'category_tree': category_tree,
        'category_index': category_index,
        'services_map': services_map,
    }
    SALON_CATALOG_CACHE[salon] = catalog
    return catalog

def _get_locations_grouped():
    """Load locations from DB grouped by country, with salon counts."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)
        cursor.execute("SELECT slug, name, country, country_code, image, status FROM locations ORDER BY sort_order, country, name")
        locations = cursor.fetchall()
        cursor.execute("SELECT location_slug, COUNT(*) as cnt FROM salons WHERE status IN ('active','planned') GROUP BY location_slug")
        counts = {r['location_slug']: r['cnt'] for r in cursor.fetchall()}
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"[Locations] Error: {e}")
        return []

    by_country = {}
    for loc in locations:
        cc = loc['country_code'] or 'uk'
        if cc not in by_country:
            by_country[cc] = {'country_code': cc, 'country': loc['country'], 'locations': []}
        by_country[cc]['locations'].append({
            'slug': loc['slug'],
            'name': loc['name'],
            'image': loc.get('image'),
            'active': loc['status'] == 'active',
            'salon_count': counts.get(loc['slug'], 0),
        })

    # Order: uk first, then ua, then pl
    order = {'uk': 0, 'ua': 1, 'pl': 2}
    return sorted(by_country.values(), key=lambda g: order.get(g['country_code'], 99))


@app.route('/')
def index():
    return redirect(url_for('select_location_lang', lang='en'))


@app.route('/update_freetime', methods=['GET'])
def update_freetime_endpoint():
    """Refresh freetime. ?salon=<code> для одного салону (швидко),
    без параметра — всі салони (батч для cron)."""
    salon_code = (request.args.get('salon') or '').strip() or None
    try:
        result = update_free_time(salon_code=salon_code)
        return jsonify({"status": "success", "data": result}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ----------- Главный маршрут (по-старому /london), теперь зададим язык "UA" -----------
@app.route('/london')
def booking():
    get_salon_catalog('l1')

    # lang="UA" — пусть считается, что "london" у нас выводит украинскую версию
    return render_template(
        'booking.html',
        category_tree=[],
        lang="UA",
        salon="l1",
        salon_info=get_salon_info_all().get('l1'),
        salon_info_all=get_salon_info_all(),
        current_salon='l1'
    )


@app.route('/<lang>')
def select_location_lang(lang):
    # If the value is a salon code, delegate to salon_index
    if lang in get_salon_dirs():
        return redirect(url_for('booking_salon_lang', salon=lang, lang='en'))
    lang = lang.upper()
    if lang not in ['EN', 'RU', 'UA', 'PL']:
        return redirect(url_for('select_location_lang', lang='en'))
    with open(os.path.join(TRANSLATION_DIR, f'{lang.lower()}.json'), encoding='utf-8') as f:
        translations = json.load(f)

    location_groups = _get_locations_grouped()
    # Collect all unique languages across all locations
    all_location_langs = set()
    for g in location_groups:
        for loc in g['locations']:
            loc_langs = _salon_cache.get('location_langs', {}).get(loc['slug'], ['en'])
            all_location_langs.update(loc_langs)
    enabled_languages = sorted(all_location_langs, key=lambda x: ['en', 'ru', 'ua', 'pl'].index(x) if x in ['en', 'ru', 'ua', 'pl'] else 99)

    # Salon-rich hierarchy для нового UX: country dropdown → city groups → salon cards
    hierarchy = get_salons_hierarchy()
    info_all = get_salon_info_all()
    for country in hierarchy:
        for city in country['cities']:
            for s in city['salons']:
                full = info_all.get(s['code']) or {}
                s['image'] = full.get('image') or ''
                s['address_full'] = (full.get('address') or {}).get('line') or s.get('address') or ''
                s['nameLocation'] = full.get('nameLocation') or s['name']

    # Активна країна через ?country=gb|ua|pl. Default = перша зі списку.
    available_countries = [c['country'] for c in hierarchy]
    country_param = (request.args.get('country') or '').lower()
    if country_param not in available_countries:
        country_param = available_countries[0] if available_countries else 'gb'

    return render_template(
        'select_location.html',
        lang=lang,
        translations=translations,
        location_groups=location_groups,
        salons_hierarchy=hierarchy,
        active_country=country_param,
        enabled_languages=enabled_languages,
    )


@app.route('/location/<location_slug>/<lang>')
def select_salon_for_location(location_slug, lang):
    lang = lang.upper()
    if lang not in ['EN', 'RU', 'UA', 'PL']:
        lang = 'EN'
    with open(os.path.join(TRANSLATION_DIR, f'{lang.lower()}.json'), encoding='utf-8') as f:
        translations = json.load(f)

    all_info = get_salon_info_all()
    _load_salon_cache()

    # Find salons belonging to this location
    location_salons = []
    for code, row in _salon_cache['salons'].items():
        if row.get('location_slug') == location_slug:
            info = all_info.get(code)
            if info:
                location_salons.append((code, info))

    if not location_salons:
        return redirect(url_for('select_location_lang', lang=lang.lower()))

    # Sort by sort_order
    location_salons.sort(key=lambda x: _salon_cache['salons'].get(x[0], {}).get('sort_order', 0))

    # If only one salon, redirect directly to booking
    if len(location_salons) == 1:
        return redirect(url_for('booking_salon_lang', salon=location_salons[0][0], lang=lang.lower()))

    # Get location display name from translations
    loc_key = 'city' + location_slug.capitalize()
    location_display_name = translations.get(loc_key, location_slug.capitalize())

    enabled_languages = _salon_cache['location_langs'].get(location_slug, ['en'])

    # If selected language is not enabled for this location, redirect to first enabled
    if lang.lower() not in enabled_languages:
        return redirect(url_for('select_salon_for_location', location_slug=location_slug, lang=enabled_languages[0]))

    return render_template(
        'select_salon.html',
        lang=lang,
        translations=translations,
        location_salons=location_salons,
        location_display_name=location_display_name,
        location_slug=location_slug,
        enabled_languages=enabled_languages,
    )

# ----------- Salon specific routes -----------
@app.route('/<salon>')
def salon_index(salon):
    if salon in get_salon_dirs():
        return redirect(url_for('booking_salon_lang', salon=salon, lang='ua'))
    return "Salon not found", 404


@app.route('/<salon>/<lang>')
def booking_salon_lang(salon, lang):
    if salon not in get_salon_dirs():
        return "Salon not found", 404
    lang = lang.upper()
    if lang not in ['EN', 'RU', 'UA', 'PL']:
        lang = 'UA'
    get_salon_catalog(salon)
    with open(os.path.join(TRANSLATION_DIR, f'{lang.lower()}.json'), encoding='utf-8') as f:
        translations = json.load(f)


    salon_info = load_salon_info(salon)
    enabled_languages = salon_info.get('languages', ['en'])

    # If selected language is not enabled for this location, redirect to first enabled
    if lang.lower() not in enabled_languages:
        return redirect(url_for('booking_salon_lang', salon=salon, lang=enabled_languages[0]))

    return render_template(
        'booking.html',
        category_tree=[],
        lang=lang,
        translations=translations,
        salon=salon,
        salon_info=salon_info,
        salon_info_all=get_salon_info_all(),
        salons_hierarchy=get_salons_hierarchy(),
        current_salon=salon,
        enabled_languages=enabled_languages,
    )

def _prune_empty_categories(nodes):
    """Видаляє категорії без послуг і без непустих children (bottom-up).

    CRM-операторам дозволено створювати порожні категорії; для UI вони
    марні — клік не дасть нічого. Працює in-place: повертає filtered list.
    """
    keep = []
    for node in nodes:
        # Спершу рекурсивно prune children
        children = node.get('children') or []
        node['children'] = _prune_empty_categories(children)
        has_services = bool(node.get('services'))
        has_children = bool(node['children'])
        if has_services or has_children:
            keep.append(node)
    return keep


def _strip_services_from_tree(nodes):
    for node in nodes:
        node.pop('services', None)
        children = node.get('children') or []
        _strip_services_from_tree(children)


@app.route('/api/salons/<salon>/employees')
def api_salon_employees(salon):
    """Return employees JSON for a salon."""
    if salon not in get_salon_dirs():
        return jsonify({'error': 'Salon not found'}), 404
    try:
        data = load_salon_json(salon, 'employees.json')
        return jsonify(data)
    except Exception as e:
        return jsonify([]), 200


@app.route('/api/salons/<salon>/freetime')
def api_salon_freetime(salon):
    """Return freetime JSON for a salon."""
    if salon not in get_salon_dirs():
        return jsonify({'error': 'Salon not found'}), 404
    try:
        data = load_salon_json(salon, 'freetime.json')
        return jsonify(data)
    except Exception as e:
        return jsonify({}), 200


@app.route('/api/salons/<salon>/translations')
def api_salon_translations(salon):
    """Get DB translations for a salon (service/category names by lang)."""
    if salon not in get_salon_dirs():
        return jsonify({'error': 'Salon not found'}), 404
    entity_type = request.args.get('entity_type')
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)
        if entity_type:
            cursor.execute(
                "SELECT entity_type, entity_id, lang, translated_name FROM translations WHERE salon_code = %s AND entity_type = %s",
                (salon, entity_type)
            )
        else:
            cursor.execute(
                "SELECT entity_type, entity_id, lang, translated_name FROM translations WHERE salon_code = %s",
                (salon,)
            )
        rows = cursor.fetchall()
        cursor.close()
        connection.close()
        # Build lookup: {entity_id: {lang: name}}
        result = {}
        for row in rows:
            eid = row['entity_id']
            if eid not in result:
                result[eid] = {}
            result[eid][row['lang']] = row['translated_name']
        return jsonify(result)
    except Exception as e:
        return jsonify({}), 200


@app.route('/api/salons/<salon>/categories')
def api_salon_categories(salon):
    if salon not in get_salon_dirs():
        return jsonify({'error': 'Salon not found'}), 404

    catalog = get_salon_catalog(salon)
    tree_copy = copy.deepcopy(catalog['category_tree'])
    # Спочатку прибираємо порожні (services + children = 0), потім стрипаємо services.
    tree_copy = _prune_empty_categories(tree_copy)
    _strip_services_from_tree(tree_copy)
    return jsonify(tree_copy)


@app.route('/api/salons/<salon>/categories/<category_id>')
def api_salon_category_detail(salon, category_id):
    if salon not in get_salon_dirs():
        return jsonify({'error': 'Salon not found'}), 404

    catalog = get_salon_catalog(salon)
    node = catalog['category_index'].get(category_id)
    if not node:
        return jsonify({'error': 'Category not found'}), 404

    return jsonify(copy.deepcopy(node))


@app.route('/success')
def success():
    # Отримуємо reference та мову з параметрів запиту
    reference = request.args.get("reference")
    # reference = "9547BFA7-6EC4-446B-AC76-61568FF470E5"

    lang = request.args.get("lang", "UA").upper()
    if lang not in ["EN", "RU", "UA", "PL"]:
        lang = "UA"
    translations_file = {
        "EN": "en.json",
        "RU": "ru.json",
        "UA": "ua.json",
        "PL": "pl.json"
    }[lang]
    with open(os.path.join(TRANSLATION_DIR, translations_file), encoding='utf-8') as f:
        translations = json.load(f)

    if not reference:
        return "Reference not provided", 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)
        sql = "SELECT * FROM appointmentsleads WHERE reference = %s"
        cursor.execute(sql, (reference,))
        record = cursor.fetchone()
        cursor.close()
        connection.close()

        if not record:
            return f"Record with reference {reference} not found", 404

        # JSONB columns у psycopg2 повертаються як dict/list — не треба json.loads.
        ci = record.get("client_info")
        client_info = json.loads(ci) if isinstance(ci, (str, bytes, bytearray)) else ci
        ap = record.get("appointments")
        appointments = json.loads(ap) if isinstance(ap, (str, bytes, bytearray)) else ap

        salon_code = client_info.get("salon")
        salon_info = get_salon_info_all().get(salon_code) if salon_code in get_salon_info_all() else None
        default_salon_info = get_salon_info_all().get('l1')
        default_map_url = "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2482.7547599389413!2d-0.14319042337927976!3d51.51771507181563!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x48761b146058a8a9%3A0xe200468b46d82427!2sPIED-DE-POULE%2067%20Mortimer%20Street!5e0!3m2!1sru!2sua!4v1741038191571!5m2!1sru!2sua"
        selected_salon_info = salon_info or default_salon_info
        map_embed_url = (selected_salon_info or {}).get("map_embed_url") or default_map_url
        map_referrerpolicy = (selected_salon_info or {}).get("map_referrerpolicy")

        # Формуємо структуру booking_data для create_appointment_service
        booking_data = {
            "reference": record.get("reference"),
            "client": client_info,
            "services": []
        }

        for appointment in appointments:
            try:
                dt = datetime.strptime(appointment.get("startTime"), "%Y-%m-%dT%H:%M:%SZ")
                dt_end = datetime.strptime(appointment.get("endTime"), "%Y-%m-%dT%H:%M:%SZ")
                duration = int((dt_end - dt).total_seconds() / 60)
            except Exception:
                continue

            if not appointment.get("services"):
                continue
            service_obj = appointment["services"][0]
            booking_service = {
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "duration": str(duration),
                "serviceName": service_obj.get("name", "Услуга"),
                "serviceId": service_obj.get("id", ""),
                "employeeName": appointment.get("specialist", {}).get("fullName", ""),
                "positionNames": appointment.get("specialist", {}).get("specialization", ""),
                "employeeId": appointment.get("specialist", {}).get("id", ""),
                "employeePhone": appointment.get("specialist", {}).get("phoneNumber", ""),
                "price": service_obj.get("price", {}).get("amount", 0),
                "currency": service_obj.get("price", {}).get("currency", ""),
                "description": appointment.get("comment", "")
            }
            booking_data["services"].append(booking_service)

        service_result = {}
        service_status = 200
        # if record.get("paymentStatus") != "paid":
        #     service_result, service_status = create_appointment_service(booking_data)
        #     try:
        #         update_conn = mysql.connector.connect(
        #             host='vs683.mirohost.net',
        #             user='u_pdpbooking',
        #             password='Pdpdatabase19',
        #             database='pdpbooking'
        #         )
        #         update_cursor = update_conn.cursor()
        #         update_sql = "UPDATE appointmentsleads SET paymentStatus=%s WHERE reference=%s"
        #         update_cursor.execute(update_sql, ("paid", reference))
        #         update_conn.commit()
        #         update_cursor.close()
        #         update_conn.close()
        #     except Exception as update_err:
        #         print(f"Error updating payment status: {update_err}")

        # # ініціалізуємо перед циклом
        # total_price = 0
        # currency = ""

        # message_lines = [
        #     "⭐️ Новий самозапис",
        #     "#web",
        #     "<b>Сайт:</b> https://p-de-p.co.uk",
        #     f"<b>Ім'я:</b> {booking_data['client'].get('name','Невідомо')}",
        #     f"<b>Телефон:</b> {booking_data['client'].get('phone','Невідомо')}",
        #     f"<b>Email:</b> {booking_data['client'].get('email','Невідомо')}",
        #     f"<b>Клієнт зазначив:</b> {booking_data['client'].get('callme','Невідомо')}",
        #     "",
        #     "Послуги:"
        # ]

        # for svc in booking_data["services"]:
        #     # збираємо саме ті рядки, що були
        #     service_line = (
        #         f"- {svc['serviceName']} від {svc['employeeName']}, "
        #         f"{svc['date']} {svc['time']}, тривалість: {svc['duration']} хв."
        #     )
        #     message_lines += [
        #         service_line,
        #         f"<b>Майстер:</b> {svc['employeeName']}",
        #         f"<b>Дата/час:</b> {datetime.strptime(svc['date']+' '+svc['time'], '%Y-%m-%d %H:%M').strftime('%d.%m.%y / %H:%M')}",
        #         f"<b>Вартість:</b> {svc['price']}"
        #     ]

        #     # ось тут нарешті додаємо до підсумку
        #     total_price += float(svc.get("price", 0))
        #     if not currency and svc.get("currency"):
        #         currency = svc["currency"]

        # # підсумкова стрічка
        # message_lines += [
        #     "",
        #     f"<b>Підсумок:</b> {total_price} {currency}"
        # ]

        # telegram_message = "\n".join(message_lines)
        # send_to_telegram(telegram_message)
        
        
        # # Підготовка повідомлення для Telegram
        # message_lines = [
        #     "✅ Новий запис",
        #     "#web",
        #     "Сайт: https://booklondon.p-de-p.com/",
        #     f"Ім'я: {booking_data.get('client', {}).get('name', 'Невідомо')}",
        #     f"Телефон: {booking_data.get('client', {}).get('phone', 'Невідомо')}",
        #     f"Email: {booking_data.get('client', {}).get('email', 'Невідомо')}",
        #     "",
        #     "Послуги:"
        # ]
        
        # # Для кожної послуги додаємо 3 рядки: опис, майстер і відформатований час
        # for svc in booking_data["services"]:
        #     # Формуємо дату/час для відображення (формат "dd.mm.yy / HH:MM")
        #     try:
        #         dt_combined = datetime.strptime(svc.get("date") + " " + svc.get("time"), "%Y-%m-%d %H:%M")
        #         date_time_formatted = dt_combined.strftime("%d.%m.%y / %H:%M")
        #     except Exception:
        #         date_time_formatted = f"{svc.get('date')} {svc.get('time')}"
        #     service_line = f"- {svc.get('serviceName')} від {svc.get('employeeName')}, {svc.get('date')} {svc.get('time')}, тривалість: {svc.get('duration')} хв."
        #     master_line = f"Майстер: {svc.get('employeeName')}"
        #     datetime_line = f"Дата/час: {date_time_formatted}"
        #     price_line = f"Вартість: {svc.get('price')}"
            
        #     message_lines.append(service_line)
        #     message_lines.append(master_line)
        #     message_lines.append(datetime_line)
        #     message_lines.append(price_line)

        #     total_price += svc.get("price", 0)
        #     if not currency and svc.get("currency"):
        #         currency = svc.get("currency")
        
        # # Додаємо підсумок
        # message_lines.append("")
        # message_lines.append(f"Підсумок: {total_price} {currency}")

        # telegram_message = "\n".join(message_lines)

        # # Відправляємо повідомлення в Telegram
        # telegram_response = send_to_telegram(telegram_message)
        # print("Telegram Response:", telegram_response)

        # Відправляємо повідомлення на емейл
        #message = "<b>Це HTML повідомлення</b><br>Надіслано через Python"
        #send_to_email("Тема листа", message, "client@gmail.com")


        # Підготовка відображення сторінки success
        customerName = client_info.get("name") or client_info.get("firstName") or "Клієнт"
        servicesSummary = ""
        cartData = []
        for appointment in appointments:
            specialist = appointment.get("specialist", {})
            if not appointment.get("services"):
                continue

            service = appointment["services"][0]
            service_name = service.get("name", "Услуга")

            try:
                dt = datetime.strptime(appointment.get("startTime"), "%Y-%m-%dT%H:%M:%SZ")
                dt_end = datetime.strptime(appointment.get("endTime"), "%Y-%m-%dT%H:%M:%SZ")
                date_str_display = dt.strftime("%d %B")
                time_str_display = dt.strftime("%H:%M")
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
                duration = int((dt_end - dt).total_seconds() / 60)
            except Exception:
                date_str_display = ""
                time_str_display = ""
                date_str = ""
                time_str = ""
                duration = ""

            price_obj = service.get("price", {})
            price_amount = price_obj.get("amount", "")
            price_currency = price_obj.get("currency", "")

            servicesSummary += f"""
            <div class="selected-service-item">
              <div class="service-summary-header">
                <div class="accordion-toggle">
                  <img src="{url_for('static', filename='img/arrowdown.svg')}" alt="Toggle Accordion">
                </div>
                <span class="cartServiceName">{service_name}</span>
              </div>
              <div class="service-summary-details" style="display: none;">
                <div class="service-info-block">
                  <div class="serviceInfo">
                    <img src="{url_for('static', filename='img/addServiceIcon.svg')}" alt="Service Icon">
                    <div class="textServiceLine">
                      <span class="cartServiceName">{service_name}</span>
                    </div>
                  </div>
                  <div class="serviceInfo">
                    <img src="{url_for('static', filename='img/avatar.png')}" alt="Master Photo" style="width:40px; height:40px; border-radius:50%;">
                    <div class="textServiceLine">
                      <span class="cartMasterName">{specialist.get('fullName', '')}</span>
                      <span class="cartMasterPosition">{specialist.get('specialization', '')}</span>
                    </div>
                  </div>
                  <div class="serviceInfo">
                    <img src="{url_for('static', filename='img/dateTimeServiceIcon.svg')}" alt="DateTime Icon">
                    <div class="textServiceLine">
                      <span class="cartServiceDate">{date_str_display}</span>
                      <span class="cartServiceTime">{time_str_display}</span>
                    </div>
                  </div>
                  <div class="serviceInfo">
                    <img src="{url_for('static', filename='img/priceServiceIcon.svg')}" alt="Price Icon">
                    <div class="textServiceLine">
                      <p class="cartServicePriceText">Вартість:</p> 
                      <span class="cartServicePrice">{price_amount} {price_currency}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            """

            cartData.append({
                "date": date_str,
                "time": time_str,
                "duration": str(duration),
                "name": service_name,
                "master": specialist.get("fullName", "")
            })

        # Передаємо також service_result, якщо потрібно відобразити додаткову інформацію
        # Get enabled languages from salon's location
        si = get_salon_info_all().get(salon_code)
        success_langs = (si.get('languages') if si else None) or ['en', 'ru', 'ua', 'pl']

        return render_template("success.html",
                               customerName=customerName,
                               servicesSummary=servicesSummary,
                               cartData=cartData,
                               service_result=service_result,
                               translations=translations,
                               lang=lang,
                               map_embed_url=map_embed_url,
                               map_referrerpolicy=map_referrerpolicy,
                               salon_code=salon_code,
                               enabled_languages=success_langs)
    except Exception as e:
        return f"Error: {str(e)}", 500


def build_category_tree(categories):
    cat_by_id = {cat['id']: cat for cat in categories}
    for cat in categories:
        cat['children'] = []

    tree = []
    for cat in categories:
        if cat.get('parent') is None:
            tree.append(cat)
        else:
            parent = cat_by_id.get(cat['parent'])
            if parent:
                parent['children'].append(cat)
    return tree

def assign_services_to_categories(category_tree, services):
    """Заполняем в каждую категорию список услуг, у которых service['category'] == cat['name']."""
    services_by_category = {}
    for service in services:
        cat_name = service.get('category')
        if not cat_name:
            continue
        services_by_category.setdefault(cat_name, []).append(service)

    def _assign(nodes):
        for node in nodes:
            node_name = node.get('name')
            node['services'] = services_by_category.get(node_name, [])
            if node.get('children'):
                _assign(node['children'])

    _assign(category_tree)
    return services_by_category

@app.route('/reserve_slot', methods=['POST'])
def reserve_slot():
    data = request.get_json() or {}
    employee_id = data.get('employeeId')
    date = data.get('date')
    time = data.get('time')
    if not all([employee_id, date, time]):
        return jsonify({'error': 'invalid'}), 400

    visit_id = request.cookies.get('visit_id')
    if add_reservation(employee_id, date, time, visit_id):
        return jsonify({'status': 'reserved'})
    else:
        return jsonify({'error': 'reserved'}), 409


@app.route('/release_slot', methods=['POST'])
def release_slot():
    data = request.get_json() or {}
    employee_id = data.get('employeeId')
    date = data.get('date')
    time = data.get('time')
    visit_id = request.cookies.get('visit_id')
    remove_reservation(employee_id, date, time, visit_id)
    return jsonify({'status': 'released'})


@app.route('/reserved_slots')
def reserved_slots():
    result = get_all_reservations()
    return jsonify(result)

@app.route('/<salon>/create_appointment', methods=['POST'])
def create_appointment(salon):
    if salon not in get_salon_dirs():
        return jsonify({"error": "Unknown salon"}), 404
    salon_info = load_salon_info(salon)
    booking_data = request.get_json() or {}
    if not isinstance(booking_data, dict):
        return jsonify({"error": "Invalid payload"}), 400

    booking_data["salon"] = salon
    booking_data.setdefault("location", copy.deepcopy(salon_info))
    
    # Если reference отсутствует или равен "None", генерируем новый и сохраняем в booking_data.
    reference = booking_data.get("reference")
    if reference is None or reference == "None":
        reference = str(uuid.uuid4()).upper()
        booking_data["reference"] = reference

    # Додаємо контакт до Mailchimp одразу при заявці з тегами мови та салону
    try:
        client_info = booking_data.get('client', {})
        name = client_info.get('name', '')
        email = client_info.get('email', '')
        
        # Збираємо категорії послуг з parent або category
        service_categories = set()
        for service in booking_data.get('services', []):
            category = service.get('parent') or service.get('category', '')
            if category:
                # Очищаємо назву категорії від зайвих символів
                clean_category = category.replace('EN ', '').replace('UA ', '').replace('RU ', '').strip()
                service_categories.add(clean_category)
        
        if name and email and service_categories:
            # Отримуємо мову та назву салону
            language = booking_data.get('lang', 'UA')
            salon_name = salon_info.get('name', salon)
            
            mailchimp_result = add_to_mailchimp(
                name, email, list(service_categories), 
                language=language, salon_name=salon_name
            )
            print(f"Mailchimp result: {mailchimp_result}")
    except Exception as e:
        print(f"Mailchimp error: {e}")

    # Теперь обе функции получат один и тот же booking_data с правильным reference.
    base_url = request.host_url.rstrip("/")
    booking_data.setdefault("base_url", base_url)

    record_result = save_appointment_record(booking_data)

    # Check if payment is enabled for this salon
    salon_row = get_salon_row(salon)
    payment_enabled = salon_row and salon_row.get('payment_enabled')

    if payment_enabled:
        # Payment flow: create payment link, redirect user to pay
        payment_result = create_payment_link(
            booking_data,
            payment_location_id=salon_info.get("payment_location_id")
        )
        appointment_id = payment_result.get("appointmentId")
        if appointment_id:
            monitor_payment_status(
                reference,
                appointment_id,
                booking_data,
                config=salon_info
            )

        final_result = {
            "record": record_result,
            "payment": payment_result
        }
    else:
        # No payment: register directly in CRM + Telegram + Email
        import threading as _apt_threading

        def _process_no_payment():
            try:
                # Update DB: mark as paid (no deposit needed)
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE appointmentsleads SET payment_status='paid', actual_state='processing' WHERE reference=%s AND actual_state='planned'",
                        (reference,)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as e:
                    print(f"[No-Payment] DB update error: {e}")

                # Create CRM appointment (database_code per salon → AIHelps auth)
                try:
                    create_appointment_service(
                        booking_data,
                        database_code=salon_info.get("database_code")
                    )
                except Exception as e:
                    print(f"[No-Payment] CRM error: {e}")

                # Send Telegram notification — per-salon token/chat_id з DB.
                # Якщо salon_info не має token/chat_id — пропускаємо (no-op).
                try:
                    tg_token = salon_info.get("telegram_token")
                    tg_chat = salon_info.get("telegram_chat_id")
                    if tg_token and tg_chat:
                        msg_lines = [
                            "✅ Нове бронювання!",
                            f"Салон: {salon_info.get('nameLocation') or salon}",
                            "",
                            f"Клієнт: {booking_data.get('client', {}).get('name', 'Невідомо')}",
                            f"Телефон: {booking_data.get('client', {}).get('phone', 'Невідомо')}",
                            f"Email: {booking_data.get('client', {}).get('email', 'Невідомо')}",
                            "",
                            "Послуги:",
                        ]
                        for svc in booking_data.get("services", []):
                            msg_lines.append(
                                f"- {svc.get('serviceName','?')} ({svc.get('employeeName','?')}), "
                                f"{svc.get('date','?')} {svc.get('time','?')}, "
                                f"{svc.get('duration','?')} хв, "
                                f"{svc.get('price','?')} {svc.get('currency','')}"
                            )
                        send_to_telegram("\n".join(msg_lines), token=tg_token, chat_id=tg_chat)
                    else:
                        print(f"[No-Payment] Skip Telegram for {salon}: no token/chat_id in DB")
                except Exception as e:
                    print(f"[No-Payment] Telegram error: {e}")

                # Send email confirmation (from-address per salon)
                try:
                    base_url = booking_data.get("base_url", "https://booking.p-de-p.com")
                    html = render_service_email_html(booking_data, base_url=base_url)
                    client_email = booking_data.get('client', {}).get('email', '')
                    if html and client_email:
                        send_to_email(
                            "Booking confirmation", html, client_email,
                            base_url=base_url,
                            from_email=salon_info.get('smtp_from_email') or None,
                        )
                except Exception as e:
                    print(f"[No-Payment] Email error: {e}")

                # Update DB: mark as created
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE appointmentsleads SET actual_state='created' WHERE reference=%s",
                        (reference,)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as e:
                    print(f"[No-Payment] DB final update error: {e}")

            except Exception as e:
                print(f"[No-Payment] Processing error: {e}")

        _apt_threading.Thread(target=_process_no_payment, daemon=True).start()

        # Return redirect to success page
        lang = booking_data.get('lang', 'en')
        final_result = {
            "record": record_result,
            "payment": {"status": "no_payment"},
            "redirect": f"/success?reference={reference}&lang={lang}"
        }

    return jsonify(final_result)
if __name__ == '__main__':
    app.run(debug=True, port=5000)
