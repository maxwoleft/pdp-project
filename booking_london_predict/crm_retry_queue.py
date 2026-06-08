"""
CRM Appointment Retry Queue System

Background worker that periodically retries failed CRM appointment creation
for paid bookings that are still in 'planned' state.
"""
import threading
import time
import json
from datetime import datetime, timedelta
import os

from _db import get_db

# Configuration
RETRY_INTERVAL_SECONDS = 300  # 5 minutes
MAX_RETRY_COUNT = 12  # ~1 hour of retries (12 * 5 min)
PROCESSING_LOCK_TIMEOUT_MINUTES = 10

# In-memory lock to prevent concurrent queue processing
_queue_lock = threading.Lock()
_queue_running = False


def get_db_connection():
    """Get Postgres connection (booking schema)."""
    return get_db()


def get_pending_retries():
    """
    Fetch appointments that need CRM retry:
    - paymentStatus = 'paid'
    - actualState = 'planned' (not 'processing' or 'created')
    - retry_count < MAX_RETRY_COUNT
    - next_retry_at is NULL or in the past

    Returns list of records and marks them as 'processing'.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)

        now = datetime.utcnow()

        # Select eligible records
        cursor.execute("""
            SELECT reference, client_info, appointments, salon_code,
                   retry_count, last_error
            FROM appointmentsleads
            WHERE payment_status = 'paid'
              AND actual_state = 'planned'
              AND (retry_count IS NULL OR retry_count < %s)
              AND (next_retry_at IS NULL OR next_retry_at <= %s)
            ORDER BY COALESCE(next_retry_at, created_at) ASC
            LIMIT 10
        """, (MAX_RETRY_COUNT, now))

        records = cursor.fetchall()

        if not records:
            return []

        # Mark these as processing to prevent other workers
        references = [r['reference'] for r in records]
        cursor.execute("""
            UPDATE appointmentsleads
            SET actual_state = 'processing'
            WHERE reference = ANY(%s)
              AND actual_state = 'planned'
        """, (references,))

        connection.commit()
        return records

    except Exception as e:
        print(f"[CRM Retry Queue] Error fetching pending retries: {e}")
        if connection:
            connection.rollback()
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def update_retry_status(reference, success, error_message=None):
    """
    Update appointment after retry attempt.

    If success: Set actualState = 'created'
    If failure: Increment retry_count, set next_retry_at, revert to 'planned'
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if success:
            cursor.execute("""
                UPDATE appointmentsleads
                SET actual_state = 'created',
                    last_error = NULL
                WHERE reference = %s
            """, (reference,))
            print(f"[CRM Retry Queue] Successfully created CRM appointment for {reference}")
        else:
            # Calculate next retry time (5 minutes from now)
            next_retry = datetime.utcnow() + timedelta(seconds=RETRY_INTERVAL_SECONDS)

            cursor.execute("""
                UPDATE appointmentsleads
                SET actual_state = 'planned',
                    retry_count = COALESCE(retry_count, 0) + 1,
                    next_retry_at = %s,
                    last_error = %s
                WHERE reference = %s
            """, (next_retry, error_message, reference))
            print(f"[CRM Retry Queue] Retry failed for {reference}, next attempt at {next_retry}")

        connection.commit()

    except Exception as e:
        print(f"[CRM Retry Queue] Error updating retry status for {reference}: {e}")
        if connection:
            connection.rollback()
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def mark_failed_permanently(reference, error_message):
    """
    Mark appointment as permanently failed after max retries.
    Sets actualState = 'failed' so it won't be retried.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE appointmentsleads
            SET actual_state = 'failed',
                last_error = %s
            WHERE reference = %s
        """, (error_message, reference))
        connection.commit()
        print(f"[CRM Retry Queue] Marked {reference} as permanently failed")

    except Exception as e:
        print(f"[CRM Retry Queue] Error marking {reference} as failed: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def _get_database_code(salon_code):
    """CRM database_code per salon — читаємо з booking.salons (не hardcoded)."""
    if not salon_code:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT database_code FROM salons WHERE code = %s", (salon_code,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[CRM Retry Queue] _get_database_code({salon_code}) error: {e}")
        return None


def _appointments_to_services(appointments):
    """Convert stored appointments format back to services format."""
    services = []
    for apt in appointments:
        specialist = apt.get('specialist', {})
        for svc in apt.get('services', []):
            # Parse start/end times
            start_time = apt.get('startTime', '')
            end_time = apt.get('endTime', '')

            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                duration = int((end_dt - start_dt).total_seconds() / 60)
                date_str = start_dt.strftime('%Y-%m-%d')
                time_str = start_dt.strftime('%H:%M')
            except Exception:
                duration = 0
                date_str = ''
                time_str = ''

            services.append({
                'serviceName': svc.get('name', ''),
                'serviceId': svc.get('id', ''),
                'category': svc.get('category', ''),
                'duration': str(duration),
                'price': str(svc.get('price', {}).get('amount', 0)),
                'currency': svc.get('price', {}).get('currency', 'GBP'),
                'employeeId': specialist.get('id', ''),
                'employeeName': specialist.get('fullName', ''),
                'positionNames': specialist.get('specialization', ''),
                'employeePhone': specialist.get('phoneNumber', ''),
                'date': date_str,
                'time': time_str,
                'description': apt.get('comment', '')
            })
    return services


def _load_salon_config(salon_code):
    """Salon config (telegram_token/chat_id, name тощо) з booking.salons —
    замінює старий data-info.json лукап."""
    if not salon_code:
        return None
    try:
        conn = get_db()
        cur = conn.cursor(dict_rows=True)
        cur.execute("""
            SELECT code, name AS "nameLocation", telegram_token, telegram_chat_id,
                   address_line, smtp_from_email
            FROM salons WHERE code = %s
        """, (salon_code,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[CRM Retry Queue] _load_salon_config({salon_code}) error: {e}")
        return None


def _notify_admin_failure(reference, client_info, error_message, salon_code):
    """Send Telegram notification to admin about permanent failure."""
    from appointmentService import send_to_telegram

    # Load salon config for Telegram credentials
    salon_config = _load_salon_config(salon_code)
    if not salon_config:
        print(f"[CRM Retry Queue] Cannot notify admin: unknown salon {salon_code}")
        return

    salon_name = salon_config.get('nameLocation', salon_code)

    message = f"""🚨 CRM APPOINTMENT CREATION FAILED

Reference: {reference}
Salon: {salon_name}

Client:
- Name: {client_info.get('name', 'N/A')}
- Phone: {client_info.get('phone', 'N/A')}
- Email: {client_info.get('email', 'N/A')}

Error: {error_message}

Перевищено максимум спроб ({MAX_RETRY_COUNT}).
Потрібне ручне втручання!"""

    try:
        send_to_telegram(
            message,
            token=salon_config.get('telegram_token'),
            chat_id=salon_config.get('telegram_chat_id')
        )
        print(f"[CRM Retry Queue] Admin notified about failure for {reference}")
    except Exception as e:
        print(f"[CRM Retry Queue] Failed to send Telegram notification: {e}")


def process_retry_queue():
    """
    Main queue processing function.
    Fetches pending retries and attempts CRM creation for each.
    """
    from appointmentService import create_appointment_service

    global _queue_running

    if not _queue_lock.acquire(blocking=False):
        print("[CRM Retry Queue] Queue processing already running, skipping...")
        return

    try:
        _queue_running = True
        records = get_pending_retries()

        if not records:
            return

        print(f"[CRM Retry Queue] Processing {len(records)} pending CRM retries...")

        for record in records:
            reference = record['reference']
            salon_code = record.get('salon_code')
            retry_count = record.get('retry_count', 0) or 0

            # Get salon info for database_code
            database_code = _get_database_code(salon_code)
            if not database_code:
                error_msg = f"Unknown salon_code: {salon_code}"
                print(f"[CRM Retry Queue] {error_msg} for {reference}")
                update_retry_status(reference, False, error_msg)
                continue

            # Reconstruct booking_data from stored JSON
            try:
                client_info = json.loads(record['client_info']) if isinstance(record['client_info'], str) else record['client_info']
                appointments = json.loads(record['appointments']) if isinstance(record['appointments'], str) else record['appointments']

                # Convert appointments back to services format
                services = _appointments_to_services(appointments)

                booking_data = {
                    'reference': reference,
                    'client': client_info,
                    'services': services,
                    'salon': salon_code
                }
            except Exception as e:
                error_msg = f"Error parsing booking data: {e}"
                print(f"[CRM Retry Queue] {error_msg} for {reference}")
                update_retry_status(reference, False, error_msg)
                continue

            # Attempt CRM creation
            try:
                print(f"[CRM Retry Queue] Attempting CRM creation for {reference} (retry #{retry_count + 1})")
                result, status_code = create_appointment_service(
                    booking_data,
                    database_code=database_code
                )

                if status_code == 200:
                    update_retry_status(reference, True)
                else:
                    error_msg = str(result.get('error', 'Unknown error'))

                    if retry_count + 1 >= MAX_RETRY_COUNT:
                        # Max retries reached - notify admin and mark failed
                        mark_failed_permanently(reference, error_msg)
                        _notify_admin_failure(reference, client_info, error_msg, salon_code)
                    else:
                        update_retry_status(reference, False, error_msg)

            except Exception as e:
                error_msg = str(e)
                print(f"[CRM Retry Queue] Exception for {reference}: {error_msg}")

                if retry_count + 1 >= MAX_RETRY_COUNT:
                    mark_failed_permanently(reference, error_msg)
                    _notify_admin_failure(reference, client_info, error_msg, salon_code)
                else:
                    update_retry_status(reference, False, error_msg)

    finally:
        _queue_running = False
        _queue_lock.release()


def start_retry_worker():
    """
    Start the background retry worker thread.
    Runs every RETRY_INTERVAL_SECONDS (5 minutes).
    """
    def _worker_loop():
        # Initial delay to let the app fully start
        time.sleep(30)

        while True:
            try:
                process_retry_queue()
            except Exception as e:
                print(f"[CRM Retry Queue] Error in retry queue worker: {e}")
            time.sleep(RETRY_INTERVAL_SECONDS)

    thread = threading.Thread(target=_worker_loop, daemon=True)
    thread.start()
    print("[CRM Retry Queue] Worker started (runs every 5 minutes)")
    return thread


def recover_stale_processing():
    """
    Recovery function to handle appointments stuck in 'processing' state.
    Called on server restart to reset appointments that were being processed
    when the server crashed.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Reset any appointments stuck in 'processing' with paid status
        # These were likely interrupted during a previous run
        cursor.execute("""
            UPDATE appointmentsleads
            SET actual_state = 'planned'
            WHERE actual_state = 'processing'
              AND payment_status = 'paid'
        """)

        affected = cursor.rowcount
        connection.commit()

        if affected > 0:
            print(f"[CRM Retry Queue] Recovered {affected} stale processing appointments")

    except Exception as e:
        print(f"[CRM Retry Queue] Error recovering stale appointments: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_pending_appointment_slots():
    """
    Get time slots from paid appointments not yet created in CRM.
    Used by app.py to block these slots from being booked by others.

    Returns: dict {employee_id: {date: [time1, time2, ...]}}
    """
    result = {}
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dict_rows=True)

        cursor.execute("""
            SELECT appointments
            FROM appointmentsleads
            WHERE payment_status = 'paid'
              AND actual_state IN ('planned', 'processing', 'failed')
        """)

        for row in cursor.fetchall():
            try:
                appointments = json.loads(row['appointments']) if isinstance(row['appointments'], str) else row['appointments']
            except Exception:
                continue

            for apt in appointments:
                emp_id = apt.get('specialist', {}).get('id')
                start_time = apt.get('startTime', '')

                if emp_id and start_time:
                    try:
                        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        date_str = dt.strftime('%Y-%m-%d')
                        time_str = dt.strftime('%H:%M')
                        result.setdefault(emp_id, {}).setdefault(date_str, []).append(time_str)
                    except Exception:
                        pass

    except Exception as e:
        print(f"[CRM Retry Queue] Error fetching pending appointment slots: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return result
