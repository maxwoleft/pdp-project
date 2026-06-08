from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import os
import json
import secrets
import string
import threading
import time
import requests as http_requests
from collections import Counter
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

from _db import IntegrityError, get_db as _get_db

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SALONS_DATA_ROOT = os.path.join('static', 'data', 'salons')


def get_db():
    """Postgres booking-schema connection (drop-in for legacy code)."""
    return _get_db()


def _get_salon_data_dir(salon_row):
    """Get filesystem path for salon data directory."""
    return os.path.join(SALONS_DATA_ROOT, salon_row['data_dir'])


def _load_locations_from_db():
    """Load all locations from DB, ordered by sort_order."""
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        cursor.execute("SELECT * FROM locations ORDER BY sort_order")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for row in rows:
            val = row.get('languages')
            if val and isinstance(val, str):
                row['languages'] = json.loads(val)
            elif val and isinstance(val, (bytes, bytearray)):
                row['languages'] = json.loads(val.decode('utf-8'))
            elif not val:
                row['languages'] = ['en']
        return rows
    except Exception as e:
        print(f"[Admin] Error loading locations: {e}")
        return []


def _load_salons_from_db(location_slug=None):
    """Load salons from DB, optionally filtered by location."""
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        if location_slug:
            cursor.execute(
                "SELECT * FROM salons WHERE location_slug = %s ORDER BY sort_order",
                (location_slug,)
            )
        else:
            cursor.execute("SELECT * FROM salons ORDER BY sort_order")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        # Parse JSON fields
        for row in rows:
            for field in ('languages', 'menu_links'):
                val = row.get(field)
                if val and isinstance(val, str):
                    row[field] = json.loads(val)
                elif val and isinstance(val, (bytes, bytearray)):
                    row[field] = json.loads(val.decode('utf-8'))
        return rows
    except Exception as e:
        print(f"[Admin] Error loading salons: {e}")
        return []


def _get_salon_by_code(code):
    """Load single salon by code."""
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        cursor.execute("SELECT * FROM salons WHERE code = %s", (code,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            for field in ('languages', 'menu_links'):
                val = row.get(field)
                if val and isinstance(val, str):
                    row[field] = json.loads(val)
                elif val and isinstance(val, (bytes, bytearray)):
                    row[field] = json.loads(val.decode('utf-8'))
        return row
    except Exception as e:
        print(f"[Admin] Error loading salon {code}: {e}")
        return None


def _get_salon_database_code(salon_code):
    """Get CRM database_code for a salon."""
    salon = _get_salon_by_code(salon_code)
    return salon['database_code'] if salon else None


def init_admin_tables():
    """No-op. Booking schema створюється через scripts/migrate_to_booking_schema.py
    і scripts/migrate_mysql_booking_data.py. Залишено для сумісності з app.py."""
    return


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_user_id' not in session:
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('admin_role') != 'superadmin':
            flash('Недостатньо прав доступу', 'error')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)
    return decorated


def _generate_temp_password(length=12):
    chars = string.ascii_letters + string.digits + '!@#$'
    return ''.join(secrets.choice(chars) for _ in range(length))


def _current_location():
    """Get current location from session, default to london."""
    slug = session.get('admin_location', 'london')
    locations = _load_locations_from_db()
    for loc in locations:
        if loc['slug'] == slug:
            return loc
    return locations[0] if locations else {'slug': 'london', 'name': 'Лондон', 'country': 'Великобританія'}


def _load_location_salons():
    """Load salons filtered by current location, as dict {code: salon_row}."""
    current_loc = _current_location()
    rows = _load_salons_from_db(current_loc['slug'])
    return {row['code']: row for row in rows}


def _admin_context():
    """Common template context for admin pages"""
    current_loc = _current_location()
    locations = _load_locations_from_db()
    return {
        'admin_email': session.get('admin_email'),
        'admin_role': session.get('admin_role'),
        'locations': locations,
        'current_location': current_loc,
    }


# ── CRM HELPERS ──

def _get_crm_token(database_code):
    """Get AIHelps CRM access token for a given database."""
    url = "https://api.aihelps.com/v1/auth/database"
    params = {
        "application_id": os.getenv('AIHELPS_APPLICATION_ID'),
        "application_secret": os.getenv('AIHELPS_APPLICATION_SECRET'),
        "database_code": database_code,
        "location": os.getenv('AIHELPS_LOCATION')
    }
    resp = http_requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _update_service_in_crm(service_id, crm_updates, database_code):
    """Send PUT request to update service in CRM. Returns True on success."""
    token = _get_crm_token(database_code)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = http_requests.put(
        f"https://api.aihelps.com/v1/services/{service_id}",
        headers=headers,
        json=crm_updates,
        timeout=15
    )
    resp.raise_for_status()
    return True


def _queue_service_update(salon_code, service_id, field_updates):
    """Add a failed CRM service update to the retry queue."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO service_update_queue (salon_code, service_id, field_updates, status)
            VALUES (%s, %s, CAST(%s AS jsonb), 'pending')
        """, (salon_code, service_id, json.dumps(field_updates, ensure_ascii=False)))
        conn.commit()
        cursor.close()
        print(f"[Service Queue] Queued update for service {service_id} (salon {salon_code})")
    except Exception as e:
        print(f"[Service Queue] Error queueing update: {e}")
    finally:
        if conn:
            conn.close()


def _process_service_update_queue():
    """Process pending service updates from the retry queue."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        cursor.execute("""
            SELECT id, salon_code, service_id, field_updates, retry_count, max_retries
            FROM service_update_queue
            WHERE status = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= NOW())
            ORDER BY created_at ASC
            LIMIT 10
        """)
        records = cursor.fetchall()
        cursor.close()
        conn.close()

        if not records:
            return

        print(f"[Service Queue] Processing {len(records)} pending service updates...")

        for record in records:
            rec_id = record['id']
            salon_code = record['salon_code']
            service_id = record['service_id']
            retry_count = record['retry_count'] or 0
            max_retries = record['max_retries'] or 10

            raw = record['field_updates']
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('utf-8')
            field_updates = json.loads(raw) if isinstance(raw, str) else raw

            database_code = _get_salon_database_code(salon_code)
            if not database_code:
                _update_queue_status(rec_id, 'failed', f'Unknown salon: {salon_code}')
                continue

            try:
                _update_service_in_crm(service_id, field_updates, database_code)
                _update_queue_status(rec_id, 'completed')
                print(f"[Service Queue] Successfully synced service {service_id}")
            except Exception as e:
                error_msg = str(e)
                if retry_count + 1 >= max_retries:
                    _update_queue_status(rec_id, 'failed', error_msg)
                    print(f"[Service Queue] Max retries reached for service {service_id}")
                else:
                    _update_queue_retry(rec_id, error_msg)
                    print(f"[Service Queue] Retry {retry_count + 1} failed for {service_id}: {error_msg}")

    except Exception as e:
        print(f"[Service Queue] Error processing queue: {e}")


def _update_queue_status(record_id, status, error=None):
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE service_update_queue SET status = %s, last_error = %s WHERE id = %s
        """, (status, error, record_id))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[Service Queue] Error updating status: {e}")
    finally:
        if conn:
            conn.close()


def _update_queue_retry(record_id, error_msg):
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE service_update_queue
            SET retry_count = retry_count + 1,
                last_error = %s,
                next_retry_at = DATE_ADD(NOW(), INTERVAL 5 MINUTE)
            WHERE id = %s
        """, (error_msg, record_id))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[Service Queue] Error updating retry: {e}")
    finally:
        if conn:
            conn.close()


def start_service_update_worker():
    """Start background worker to retry failed CRM service updates every 5 minutes."""
    def _worker():
        time.sleep(30)
        while True:
            try:
                _process_service_update_queue()
            except Exception as e:
                print(f"[Service Queue Worker] Error: {e}")
            time.sleep(300)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    print("[Service Queue] Background worker started (every 5 min)")
    return thread


# ── AUTH ──

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'admin_user_id' in session:
        return redirect(url_for('admin.dashboard'))

    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        try:
            conn = get_db()
            cursor = conn.cursor(dict_rows=True)
            cursor.execute(
                "SELECT * FROM admin_users WHERE email = %s AND is_active = TRUE",
                (email,)
            )
            user = cursor.fetchone()
            cursor.close()
            conn.close()

            if user and check_password_hash(user['password_hash'], password):
                session['admin_user_id'] = user['id']
                session['admin_email'] = user['email']
                session['admin_role'] = user['role']
                return redirect(url_for('admin.dashboard'))
            else:
                error = 'Невірний email або пароль'
        except Exception as e:
            error = 'Помилка сервера'
            print(f"Admin login error: {e}")

    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
def logout():
    session.pop('admin_user_id', None)
    session.pop('admin_email', None)
    session.pop('admin_role', None)
    return redirect(url_for('admin.login'))


# ── DASHBOARD ──

@admin_bp.route('/docs')
@login_required
def docs():
    tab = request.args.get('tab', 'tech')
    return render_template('admin/docs/index.html', active_tab=tab, **_admin_context())


@admin_bp.route('/')
@admin_bp.route('/dashboard')
@login_required
def dashboard():
    salons = _load_location_salons()
    return render_template(
        'admin/dashboard.html',
        salons=salons,
        **_admin_context()
    )


@admin_bp.route('/api/dashboard')
@login_required
def api_dashboard():
    """API endpoint for dashboard analytics data"""
    salon_filter = request.args.get('location', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    # Get salon codes for current location
    current_loc = _current_location()
    location_salon_codes = list(_load_location_salons().keys())

    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)

        # Build query for appointments
        where_clauses = []
        params = []

        if salon_filter:
            # Filter by specific salon
            where_clauses.append("salon_code = %s")
            params.append(salon_filter)
        elif location_salon_codes:
            # Filter by all salons in current location
            placeholders = ', '.join(['%s'] * len(location_salon_codes))
            where_clauses.append(f"salon_code IN ({placeholders})")
            params.extend(location_salon_codes)

        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(date_from)

        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(date_to + ' 23:59:59')

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total orders + payment stats
        cursor.execute(
            f"SELECT COUNT(*) as total, "
            f"SUM(CASE WHEN payment_status = 'paid' THEN 1 ELSE 0 END) as paid, "
            f"SUM(CASE WHEN payment_status = 'wait' THEN 1 ELSE 0 END) as waiting "
            f"FROM appointmentsleads{where_sql}",
            params
        )
        stats = cursor.fetchone()
        total_orders = stats['total'] or 0
        orders_paid = stats['paid'] or 0
        orders_waiting = stats['waiting'] or 0

        # Get all appointments data for analytics
        cursor.execute(
            f"SELECT appointments, client_info FROM appointmentsleads{where_sql}",
            params
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        # Analyze services
        service_counter = Counter()
        total_services = 0

        for row in rows:
            try:
                raw = row['appointments']
                if raw is None:
                    continue
                # Handle bytes (some mysql-connector configs return bytes)
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode('utf-8')
                appointments = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(appointments, list):
                    print(f"[Dashboard] appointments is not a list: {type(appointments)}")
                    continue
                for appt in appointments:
                    services = appt.get('services', [])
                    for svc in services:
                        name = svc.get('name') or svc.get('serviceName') or 'Невідома послуга'
                        service_counter[name] += 1
                        total_services += 1
            except Exception as e:
                print(f"[Dashboard] Error parsing appointments: {e}, raw type: {type(row.get('appointments'))}")
                continue

        top_services = [
            {'name': name, 'count': count}
            for name, count in service_counter.most_common(10)
        ]

        avg_services = round(total_services / total_orders, 1) if total_orders > 0 else 0

        return jsonify({
            'total_orders': total_orders,
            'orders_paid': int(orders_paid),
            'orders_waiting': int(orders_waiting),
            'avg_services_per_order': avg_services,
            'top_services': top_services,
        })

    except Exception as e:
        print(f"Dashboard API error: {e}")
        return jsonify({
            'total_orders': 0,
            'orders_paid': 0,
            'orders_waiting': 0,
            'avg_services_per_order': 0,
            'top_services': [],
        })


# ── LOCATIONS & SALONS ──

# All supported languages with display labels
AVAILABLE_LANGUAGES = [
    {'code': 'en', 'label': 'English', 'prefix': 'EN'},
    {'code': 'ua', 'label': 'Українська', 'prefix': 'UA'},
    {'code': 'ru', 'label': 'Русский', 'prefix': 'RUS'},
    {'code': 'pl', 'label': 'Polski', 'prefix': 'PL'},
]


@admin_bp.route('/api/salons/ai-chat-bulk', methods=['POST'])
@login_required
@superadmin_required
def api_salons_ai_chat_bulk():
    """Bulk-toggle AI chat banner for ALL salons. Body: {enabled: true|false}."""
    data = request.get_json() or {}
    enabled = bool(data.get('enabled'))
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE salons SET ai_chat_enabled = %s", (enabled,))
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, 'reload_salon_cache'):
            app_mod.reload_salon_cache()
        return jsonify({'success': True, 'updated': affected, 'enabled': enabled})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/switch-location', methods=['POST'])
@login_required
def switch_location():
    """Switch active location in session."""
    data = request.get_json()
    slug = data.get('slug', 'london')
    locations = _load_locations_from_db()
    valid_slugs = {loc['slug'] for loc in locations}
    if slug not in valid_slugs:
        return jsonify({'error': 'Invalid location'}), 400
    session['admin_location'] = slug
    return jsonify({'success': True, 'location': slug})


@admin_bp.route('/salons')
@login_required
def salons():
    current_loc = _current_location()
    salons_list = _load_salons_from_db(current_loc['slug'])
    return render_template(
        'admin/salons.html',
        salons=salons_list,
        available_languages=AVAILABLE_LANGUAGES,
        **_admin_context()
    )


@admin_bp.route('/api/salons/<salon_code>', methods=['GET'])
@login_required
def api_get_salon(salon_code):
    """Get salon details."""
    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Salon not found'}), 404
    return jsonify(salon)


@admin_bp.route('/api/salons/<salon_code>', methods=['PUT'])
@login_required
def api_update_salon(salon_code):
    """Update salon settings."""
    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Salon not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    # Allowed fields to update
    allowed_fields = [
        'name', 'address_line', 'postal_code', 'phone_display', 'phone_link',
        'email', 'database_code', 'payment_location_id', 'image', 'area_icon',
        'map_embed_url', 'map_link', 'telegram_token', 'telegram_chat_id',
        'whatsapp_phone', 'instagram_url', 'hours_label', 'hours_value',
        'smtp_from_email',
        'ai_chat_enabled',
        'payment_enabled', 'payment_system_id',
        'status', 'sort_order',
    ]

    updates = {}
    for field in allowed_fields:
        if field in data:
            updates[field] = data[field]

    # languages are now managed at location level, ignore if sent
    data.pop('languages', None)

    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        set_clause = ', '.join(f'{k} = %s' for k in updates)
        values = list(updates.values()) + [salon_code]
        cursor.execute(f"UPDATE salons SET {set_clause} WHERE code = %s", values)
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/salons/<salon_code>/languages', methods=['GET'])
@login_required
def api_get_languages(salon_code):
    """Get configured languages for a salon (reads from location)."""
    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Invalid salon'}), 400
    # Languages now come from the location
    locations = _load_locations_from_db()
    loc = next((l for l in locations if l['slug'] == salon.get('location_slug')), None)
    langs = (loc.get('languages') if loc else None) or ['en']
    return jsonify({'languages': langs})


@admin_bp.route('/api/locations/<slug>/languages', methods=['GET'])
@login_required
def api_get_location_languages(slug):
    """Get configured languages for a location."""
    locations = _load_locations_from_db()
    loc = next((l for l in locations if l['slug'] == slug), None)
    if not loc:
        return jsonify({'error': 'Location not found'}), 404
    return jsonify({'languages': loc.get('languages') or ['en']})


@admin_bp.route('/api/locations/<slug>/languages', methods=['PUT'])
@login_required
def api_set_location_languages(slug):
    """Set configured languages for a location."""
    data = request.get_json()
    languages = data.get('languages', [])
    if not languages:
        return jsonify({'error': 'At least one language required'}), 400

    valid_codes = {l['code'] for l in AVAILABLE_LANGUAGES}
    languages = [l for l in languages if l in valid_codes]
    if not languages:
        return jsonify({'error': 'No valid languages'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE locations SET languages = CAST(%s AS jsonb) WHERE slug = %s",
            (json.dumps(languages, ensure_ascii=False), slug)
        )
        conn.commit()
        cursor.close()
        conn.close()
        # Invalidate salon cache so client-facing app picks up new languages
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, 'reload_salon_cache'):
            app_mod.reload_salon_cache()
            print(f"[Admin] Salon cache reloaded after language change for {slug}")
        return jsonify({'success': True, 'languages': languages})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/locations', methods=['POST'])
@login_required
@superadmin_required
def api_create_location():
    """Create a new location."""
    data = request.get_json()
    slug = data.get('slug', '').strip().lower()
    name = data.get('name', '').strip()
    country = data.get('country', '').strip()
    country_code = data.get('country_code', '').strip().lower()

    if not slug or not name or not country:
        return jsonify({'error': 'slug, name, country обовʼязкові'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO locations (slug, name, country, country_code, status, sort_order) "
            "VALUES (%s, %s, %s, %s, 'planned', (SELECT COALESCE(MAX(t.sort_order),0)+1 FROM locations t))",
            (slug, name, country, country_code)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'slug': slug})
    except IntegrityError:
        return jsonify({'error': f'Локація "{slug}" вже існує'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/locations/<slug>', methods=['PUT'])
@login_required
@superadmin_required
def api_update_location(slug):
    """Update location settings."""
    data = request.get_json()
    allowed = ['name', 'country', 'country_code', 'image', 'status', 'sort_order']
    updates = {k: data[k] for k in allowed if k in data}

    # Handle languages (JSON field)
    if 'languages' in data:
        langs = data['languages']
        if isinstance(langs, list):
            valid_codes = {l['code'] for l in AVAILABLE_LANGUAGES}
            langs = [l for l in langs if l in valid_codes]
            if not langs:
                return jsonify({'error': 'At least one valid language required'}), 400
            updates['languages'] = json.dumps(langs, ensure_ascii=False)

    if not updates:
        return jsonify({'error': 'No valid fields'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        set_clause = ', '.join(
            f'{k} = CAST(%s AS jsonb)' if k == 'languages' else f'{k} = %s'
            for k in updates
        )
        values = list(updates.values()) + [slug]
        cursor.execute(f"UPDATE locations SET {set_clause} WHERE slug = %s", values)
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── TRANSLATIONS ──

@admin_bp.route('/api/translations', methods=['GET'])
@login_required
def api_get_translations():
    """Get translations for a salon. Query: ?salon=<code>&entity_type=<service|category>"""
    salon_code = request.args.get('salon')
    entity_type = request.args.get('entity_type')
    if not salon_code:
        return jsonify({'error': 'salon required'}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        if entity_type:
            cursor.execute(
                "SELECT * FROM translations WHERE salon_code = %s AND entity_type = %s",
                (salon_code, entity_type)
            )
        else:
            cursor.execute(
                "SELECT * FROM translations WHERE salon_code = %s",
                (salon_code,)
            )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/translations', methods=['PUT'])
@login_required
def api_upsert_translation():
    """Upsert a translation. Body: {salon_code, entity_type, entity_id, lang, translated_name, translated_description?}"""
    data = request.get_json()
    required = ['salon_code', 'entity_type', 'entity_id', 'lang', 'translated_name']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    if data['entity_type'] not in ('service', 'category'):
        return jsonify({'error': 'entity_type must be service or category'}), 400

    valid_codes = {l['code'] for l in AVAILABLE_LANGUAGES}
    if data['lang'] not in valid_codes:
        return jsonify({'error': 'Invalid language'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO translations (salon_code, entity_type, entity_id, lang, translated_name, translated_description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (salon_code, entity_type, entity_id, lang) DO UPDATE
                SET translated_name = EXCLUDED.translated_name,
                    translated_description = EXCLUDED.translated_description,
                    updated_at = NOW()
        """, (
            data['salon_code'], data['entity_type'], data['entity_id'],
            data['lang'], data['translated_name'], data.get('translated_description', '')
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/translations/batch', methods=['PUT'])
@login_required
def api_batch_upsert_translations():
    """Batch upsert translations. Body: {translations: [{salon_code, entity_type, entity_id, lang, translated_name}, ...]}"""
    data = request.get_json()
    items = data.get('translations', [])
    if not items:
        return jsonify({'error': 'No translations provided'}), 400

    valid_codes = {l['code'] for l in AVAILABLE_LANGUAGES}
    try:
        conn = get_db()
        cursor = conn.cursor()
        for item in items:
            if not all(item.get(f) for f in ('salon_code', 'entity_type', 'entity_id', 'lang', 'translated_name')):
                continue
            if item['lang'] not in valid_codes:
                continue
            if item['entity_type'] not in ('service', 'category'):
                continue
            cursor.execute("""
                INSERT INTO translations (salon_code, entity_type, entity_id, lang, translated_name, translated_description)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (salon_code, entity_type, entity_id, lang) DO UPDATE
                    SET translated_name = EXCLUDED.translated_name,
                        translated_description = EXCLUDED.translated_description,
                        updated_at = NOW()
            """, (
                item['salon_code'], item['entity_type'], item['entity_id'],
                item['lang'], item['translated_name'], item.get('translated_description', '')
            ))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'count': len(items)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/translations/<int:translation_id>', methods=['DELETE'])
@login_required
def api_delete_translation(translation_id):
    """Delete a translation."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM translations WHERE id = %s", (translation_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PAYMENT SYSTEMS ──

def _load_payment_systems():
    """Load all payment systems from DB."""
    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        cursor.execute("SELECT * FROM payment_systems ORDER BY id")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for row in rows:
            val = row.get('config')
            if val and isinstance(val, str):
                row['config'] = json.loads(val)
            elif val and isinstance(val, (bytes, bytearray)):
                row['config'] = json.loads(val.decode('utf-8'))
        return rows
    except Exception as e:
        print(f"[Admin] Error loading payment systems: {e}")
        return []


@admin_bp.route('/payment-systems')
@login_required
def payment_systems():
    systems = _load_payment_systems()
    return render_template(
        'admin/payment_systems.html',
        payment_systems=systems,
        **_admin_context()
    )


@admin_bp.route('/api/payment-systems', methods=['GET'])
@login_required
def api_list_payment_systems():
    """List all payment systems."""
    systems = _load_payment_systems()
    return jsonify(systems)


@admin_bp.route('/api/payment-systems', methods=['POST'])
@login_required
@superadmin_required
def api_create_payment_system():
    """Create a new payment system."""
    data = request.get_json()
    name = data.get('name', '').strip()
    provider = data.get('provider', '').strip()
    config = data.get('config', {})

    if not name or not provider:
        return jsonify({'error': 'name та provider обовʼязкові'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO payment_systems (name, provider, config) VALUES (%s, %s, CAST(%s AS jsonb)) RETURNING id",
            (name, provider, json.dumps(config, ensure_ascii=False))
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'id': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/payment-systems/<int:system_id>', methods=['PUT'])
@login_required
@superadmin_required
def api_update_payment_system(system_id):
    """Update a payment system."""
    data = request.get_json()
    updates = {}
    for field in ('name', 'provider', 'status'):
        if field in data:
            updates[field] = data[field]
    if 'config' in data:
        updates['config'] = json.dumps(data['config'], ensure_ascii=False)

    if not updates:
        return jsonify({'error': 'No valid fields'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        set_clause = ', '.join(
            f'{k} = CAST(%s AS jsonb)' if k == 'config' else f'{k} = %s'
            for k in updates
        )
        values = list(updates.values()) + [system_id]
        cursor.execute(f"UPDATE payment_systems SET {set_clause} WHERE id = %s", values)
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/payment-systems/<int:system_id>', methods=['DELETE'])
@login_required
@superadmin_required
def api_delete_payment_system(system_id):
    """Delete a payment system (only if not used by any salon)."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM salons WHERE payment_system_id = %s", (system_id,))
        count = cursor.fetchone()[0]
        if count > 0:
            cursor.close()
            conn.close()
            return jsonify({'error': f'Система використовується в {count} салонах'}), 409

        cursor.execute("DELETE FROM payment_systems WHERE id = %s", (system_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── SERVICES ──

@admin_bp.route('/services')
@login_required
def services():
    salons = _load_location_salons()
    return render_template(
        'admin/services.html',
        active_tab='general',
        salons=salons,
        **_admin_context()
    )


@admin_bp.route('/services/categories')
@login_required
def categories():
    salons = _load_location_salons()
    return render_template(
        'admin/services.html',
        active_tab='categories',
        salons=salons,
        **_admin_context()
    )




@admin_bp.route('/services/recommendations')
@login_required
def recommendations():
    salons = _load_location_salons()
    return render_template(
        'admin/services.html',
        active_tab='recommendations',
        salons=salons,
        **_admin_context()
    )


@admin_bp.route('/api/services')
@login_required
def api_services():
    """API: get services list for a salon."""
    salon_code = request.args.get('salon', 'l1')
    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Invalid salon'}), 400

    folder = _get_salon_data_dir(salon)
    try:
        services_path = os.path.join(BASE_DIR, folder, 'services.json')
        categories_path = os.path.join(BASE_DIR, folder, 'categories.json')

        with open(services_path, encoding='utf-8') as f:
            svcs = json.load(f)
        with open(categories_path, encoding='utf-8') as f:
            cats = json.load(f)

        # Filter out archived services
        svcs = [s for s in svcs if not s.get('archive')]

        # Language config from location
        locations = _load_locations_from_db()
        loc = next((l for l in locations if l['slug'] == salon.get('location_slug')), None)
        languages = (loc.get('languages') if loc else None) or ['en']
        lang_map = {l['code']: l for l in AVAILABLE_LANGUAGES}
        lang_config = [lang_map[c] for c in languages if c in lang_map]

        return jsonify({
            'services': svcs,
            'categories': cats,
            'languages': lang_config,
        })
    except Exception as e:
        print(f"[Admin API] Error loading services: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/services/<service_id>', methods=['PUT'])
@login_required
def api_update_service(service_id):
    """API: update a service (local JSON + CRM sync)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    salon_code = data.get('salon', 'l1')
    updates = data.get('updates', {})

    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Invalid salon'}), 400
    folder = _get_salon_data_dir(salon)

    # 1) Update local JSON file
    services_path = os.path.join(BASE_DIR, folder, 'services.json')
    try:
        with open(services_path, encoding='utf-8') as f:
            svcs = json.load(f)

        found = False
        location_position = None
        for svc in svcs:
            if svc.get('id') == service_id:
                if 'name' in updates:
                    svc['name'] = updates['name']
                if 'description' in updates:
                    svc['description'] = updates['description']
                if 'duration' in updates:
                    svc['duration'] = int(updates['duration'])
                location_position = svc.get('location_position')
                found = True
                break

        if not found:
            return jsonify({'error': 'Service not found'}), 404

        with open(services_path, 'w', encoding='utf-8') as f:
            json.dump(svcs, f, ensure_ascii=False, indent=4)

    except Exception as e:
        print(f"[Admin API] Error updating local JSON: {e}")
        return jsonify({'error': str(e)}), 500

    # 2) Sync to CRM
    database_code = salon.get('database_code')
    if not database_code:
        return jsonify({'success': True, 'crm_synced': False})

    crm_updates = {}
    if 'name' in updates:
        crm_updates['name'] = updates['name']
    if 'description' in updates:
        crm_updates['description'] = updates['description']
    if 'duration' in updates:
        crm_updates['duration'] = int(updates['duration'])
    if not crm_updates:
        return jsonify({'success': True, 'crm_synced': False})

    try:
        _update_service_in_crm(service_id, crm_updates, database_code)
        return jsonify({'success': True, 'crm_synced': True})
    except Exception as e:
        print(f"[Admin API] CRM update failed, queueing: {e}")
        _queue_service_update(salon_code, service_id, crm_updates)
        return jsonify({'success': True, 'crm_synced': False, 'queued': True})


@admin_bp.route('/api/categories/<category_id>', methods=['PUT'])
@login_required
def api_update_category(category_id):
    """API: update a category name (local JSON + CRM sync)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    salon_code = data.get('salon', 'l1')
    new_name = data.get('name', '')

    salon = _get_salon_by_code(salon_code)
    if not salon:
        return jsonify({'error': 'Invalid salon'}), 400
    folder = _get_salon_data_dir(salon)

    if not new_name.strip():
        return jsonify({'error': 'Name is required'}), 400

    # 1) Update categories.json
    categories_path = os.path.join(BASE_DIR, folder, 'categories.json')
    try:
        with open(categories_path, encoding='utf-8') as f:
            cats = json.load(f)

        found = False
        old_name = None
        for cat in cats:
            if cat.get('id') == category_id:
                old_name = cat.get('name')
                cat['name'] = new_name
                found = True
                break

        if not found:
            return jsonify({'error': 'Category not found'}), 404

        with open(categories_path, 'w', encoding='utf-8') as f:
            json.dump(cats, f, ensure_ascii=False, indent=4)

        # 2) Update category name references in services.json
        #    (services store category/parent as name strings)
        if old_name and old_name != new_name:
            services_path = os.path.join(BASE_DIR, folder, 'services.json')
            try:
                with open(services_path, encoding='utf-8') as f:
                    svcs = json.load(f)
                for svc in svcs:
                    if svc.get('category') == old_name:
                        svc['category'] = new_name
                    if svc.get('parent') == old_name:
                        svc['parent'] = new_name
                # Also update parentName in categories
                for cat in cats:
                    if cat.get('parentName') == old_name:
                        cat['parentName'] = new_name
                with open(services_path, 'w', encoding='utf-8') as f:
                    json.dump(svcs, f, ensure_ascii=False, indent=4)
                with open(categories_path, 'w', encoding='utf-8') as f:
                    json.dump(cats, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"[Admin API] Error updating service refs: {e}")

    except Exception as e:
        print(f"[Admin API] Error updating category: {e}")
        return jsonify({'error': str(e)}), 500

    # 3) Sync to CRM
    database_code = salon.get('database_code')
    if not database_code:
        return jsonify({'success': True, 'crm_synced': False})

    crm_updates = {'name': new_name}
    try:
        token = _get_crm_token(database_code)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = http_requests.put(
            f"https://api.aihelps.com/v1/services/categories/{category_id}",
            headers=headers,
            json=crm_updates,
            timeout=15
        )
        resp.raise_for_status()
        return jsonify({'success': True, 'crm_synced': True})
    except Exception as e:
        print(f"[Admin API] CRM category update failed, queueing: {e}")
        _queue_service_update(salon_code, f"cat_{category_id}", crm_updates)
        return jsonify({'success': True, 'crm_synced': False, 'queued': True})


# ── USERS ──

@admin_bp.route('/users', methods=['GET', 'POST'])
@login_required
@superadmin_required
def users():
    message = None
    message_type = None
    user_list = []

    try:
        conn = get_db()
        cursor = conn.cursor(dict_rows=True)
        cursor.execute(
            "SELECT id, email, role, is_active, created_at FROM admin_users ORDER BY created_at DESC"
        )
        user_list = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        message = 'Помилка завантаження користувачів'
        message_type = 'error'
        print(f"Admin users list error: {e}")

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            new_email = request.form.get('email', '').strip().lower()
            new_role = request.form.get('role', 'admin')
            new_password = request.form.get('password', '').strip()

            if not new_email or not new_password:
                message = 'Email та пароль обовʼязкові'
                message_type = 'error'
            elif new_role not in ('superadmin', 'admin'):
                message = 'Невірна роль'
                message_type = 'error'
            else:
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute(
                        """INSERT INTO admin_users (email, password_hash, role, created_by)
                           VALUES (%s, %s, %s, %s)""",
                        (new_email, generate_password_hash(new_password),
                         new_role, session['admin_user_id'])
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = f'Користувача {new_email} додано'
                    message_type = 'success'

                    conn = get_db()
                    cursor = conn.cursor(dict_rows=True)
                    cursor.execute(
                        "SELECT id, email, role, is_active, created_at FROM admin_users ORDER BY created_at DESC"
                    )
                    user_list = cursor.fetchall()
                    cursor.close()
                    conn.close()
                except IntegrityError:
                    message = f'Користувач з email {new_email} вже існує'
                    message_type = 'error'
                except Exception as e:
                    message = 'Помилка при додаванні користувача'
                    message_type = 'error'
                    print(f"Add admin user error: {e}")

        elif action == 'toggle':
            target_id = request.form.get('user_id')
            if str(target_id) == str(session['admin_user_id']):
                message = 'Не можна деактивувати власний акаунт'
                message_type = 'error'
            else:
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE admin_users SET is_active = NOT is_active WHERE id = %s",
                        (target_id,)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = 'Статус оновлено'
                    message_type = 'success'

                    conn = get_db()
                    cursor = conn.cursor(dict_rows=True)
                    cursor.execute(
                        "SELECT id, email, role, is_active, created_at FROM admin_users ORDER BY created_at DESC"
                    )
                    user_list = cursor.fetchall()
                    cursor.close()
                    conn.close()
                except Exception as e:
                    message = 'Помилка оновлення статусу'
                    message_type = 'error'
                    print(f"Toggle admin user error: {e}")

    return render_template(
        'admin/users.html',
        current_user_id=session.get('admin_user_id'),
        user_list=user_list,
        message=message,
        message_type=message_type,
        **_admin_context()
    )
