# на початку файлу
import time, json, requests
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote_plus, quote, urlencode
from typing import Optional
import os
from dotenv import load_dotenv

from _db import get_db

# Завантажуємо змінні з .env файлу
load_dotenv()

import ssl
try:
    import certifi
    _CERT_PATH = certifi.where()
except Exception:
    _CERT_PATH = None
#TEST email
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr, formatdate, make_msgid
try:
    from email_validator import validate_email, EmailNotValidError
except ImportError:
    validate_email = None
    EmailNotValidError = Exception
import mimetypes
import os
from jinja2 import Environment, FileSystemLoader
from email.mime.image import MIMEImage
#END TEST email


def guess_image_subtype(data: bytes) -> Optional[str]:
    # JPEG
    if data.startswith(b'\xFF\xD8\xFF'):
        return 'jpeg'
    # PNG
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    # GIF
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    # BMP
    if data.startswith(b'BM'):
        return 'bmp'
    # TIFF (little/big endian)
    if data.startswith((b'II*\x00', b'MM\x00*')):
        return 'tiff'
    return None

# ============================
# Функция для сохранения заявки в локальную базу (MySQL) с использованием mysql.connector
def save_appointment_record(booking_data):
    try:
        print(f"Saving appointment with reference: {booking_data.get('reference')}")
        # Если reference уже есть в booking_data, используем его, иначе генерируем и сохраняем
        reference = booking_data.get("reference")
        if not reference:
            reference = str(uuid.uuid4()).upper()
            booking_data["reference"] = reference

        client = booking_data.get("client", {})
        services = booking_data.get("services", [])
        
        if not client or not services:
            return {"status": "error", "message": "Missing client or services data"}

        client_info = {
            "name": client.get("name"),
            "phone": client.get("phone"),
            "email": client.get("email"),
            "callme": client.get("callme", False)
        }

        salon_code = booking_data.get("salon")
        if salon_code:
            client_info["salon"] = salon_code
        
        appointments = []
        categories = []
        for svc in services:
            start_dt = datetime.strptime(f"{svc.get('date')} {svc.get('time')}", "%Y-%m-%d %H:%M")
            duration = int(svc.get("duration", "0"))
            end_dt = start_dt + timedelta(minutes=duration)
            
            service_obj = {
                "name": svc.get("serviceName"),
                "price": {
                    "amount": float(svc.get("price", 0)),
                    "currency": svc.get("currency"),
                },
                "count": 1,
                "category": svc.get("parent") or svc.get("category"),
            }
            appointment = {
                "specialist": {
                    "fullName": svc.get("employeeName"),
                    "specialization": svc.get("positionNames"),
                    "id": svc.get("employeeId"),
                    "phoneNumber": svc.get("employeePhone"),
                },
                "startTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "comment": svc.get("description", ""),
                "services": [service_obj],
            }
            appointments.append(appointment)
            category_name = service_obj.get("category")
            if category_name:
                categories.append(category_name)

        servcategory = ", ".join(sorted(set(categories))) if categories else None
        
        client_info_str = json.dumps(client_info)
        appointments_str = json.dumps(appointments)

        connection = get_db()
        cursor = connection.cursor()

        # Перевіряємо, чи вже існує запис з таким reference (захист від дублів)
        cursor.execute("SELECT reference FROM appointmentsleads WHERE reference = %s LIMIT 1", (reference,))
        existing = cursor.fetchone()

        if existing:
            cursor.close()
            connection.close()
            print(f"Duplicate detected for reference {reference}, skipping insert")
            return {"status": "success", "reference": reference, "duplicate": True}

        # Отримуємо salon_code з client_info для retry queue
        salon_code = client_info.get("salon")

        sql = """
            INSERT INTO appointmentsleads
                (reference, client_info, appointments, actual_state, payment_status, servcategory, salon_code)
            VALUES (%s, CAST(%s AS jsonb), CAST(%s AS jsonb), %s, %s, %s, %s)
        """
        params = (
            reference,
            client_info_str,
            appointments_str,
            "planned",
            "wait",
            servcategory,
            salon_code,
        )

        cursor.execute(sql, params)
        connection.commit()
        cursor.close()
        connection.close()

        return {"status": "success", "reference": reference}
    
    except Exception as e:
        print(f"Error saving appointment: {str(e)}")
        return {"status": "error", "message": f"Error saving appointment: {str(e)}"}
    
def is_valid_recipient(addr: str) -> bool:
    if not addr:
        return False
    if validate_email is None:
        return bool(addr and "@" in addr and "." in addr.split("@")[-1])
    try:
        validate_email(addr, check_deliverability=True)
        return True
    except EmailNotValidError:
        return False


def send_to_email(subject, message, to_email, *, base_url="https://booklondon.p-de-p.com", from_email=None):
    """Send HTML email with inline images.

    `from_email` — opt override per-salon (booking.salons.smtp_from_email). Якщо
    не передано — береться з env SMTP_FROM_EMAIL.
    """
    def _is_valid_recipient(addr: str) -> bool:
        if validate_email is None:
            return bool(addr and "@" in addr and "." in addr.split("@")[-1])
        try:
            validate_email(addr, check_deliverability=True)
            return True
        except EmailNotValidError:
            return False

    # --- Налаштування SMTP з .env файлу --------------------------
    smtp_server = os.getenv('SMTP_SERVER')
    smtp_port   = int(os.getenv('SMTP_PORT', 587))
    from_name   = os.getenv('SMTP_FROM_NAME')
    if not from_email:
        from_email = os.getenv('SMTP_FROM_EMAIL')
    password    = os.getenv('SMTP_PASSWORD')

    # 1) Перевірка адреси одержувача
    if not _is_valid_recipient(to_email):
        print(f"Skip email: invalid or non-deliverable recipient: {to_email}")
        return False

    # 2) Побудова MIME-повідомлення з коректними заголовками
    msg_root = MIMEMultipart("related")
    msg_root["From"] = formataddr((from_name, from_email))
    msg_root["To"] = to_email
    msg_root["Subject"] = subject
    msg_root["Date"] = formatdate(localtime=True)
    msg_root["Message-ID"] = make_msgid(domain=from_email.split("@")[-1])
    # msg_root["Reply-To"] = from_email  # за потреби

    msg_alt = MIMEMultipart("alternative")
    msg_root.attach(msg_alt)

    attachments = []

    # 3) Замінюємо <img src="..."> на cid:... і збираємо вкладення
    def replace_src(match):
        src = match.group(2)
        rel_path = None
        if src.startswith(base_url):
            rel_path = src[len(base_url):].lstrip("/")
        elif src.startswith("/static/"):
            rel_path = src.lstrip("/")
        elif src.startswith("./imgs/") or src.startswith("imgs/"):
            rel_path = os.path.join("emails", src.lstrip("./"))
        elif src.startswith("emails/"):
            rel_path = src

        if not rel_path:
            return match.group(0)

        file_path = os.path.join(os.path.dirname(__file__), rel_path)
        if not os.path.isfile(file_path):
            return match.group(0)

        cid = f"image{len(attachments)}@inline"
        with open(file_path, "rb") as f:
            data = f.read()

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type and mime_type.startswith("image/"):
            maintype, subtype = mime_type.split("/", 1)
        else:
            # fallback на сніффінг, очікуєш, що guess_image_subtype визначено вище у модулі
            subtype = guess_image_subtype(data) or "octet-stream"
            maintype = "image" if subtype != "octet-stream" else "application"

        if maintype == "image":
            part = MIMEImage(data, _subtype=subtype)
        else:
            part = MIMEBase(maintype, subtype)
            part.set_payload(data)
            encoders.encode_base64(part)

        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", "inline", filename=os.path.basename(file_path))
        attachments.append(part)
        return f'{match.group(1)}cid:{cid}{match.group(3)}'

    img_regex = re.compile(r'(<img[^>]*\bsrc=[\'\"])([^\'\"]+)([\'\"])', re.IGNORECASE)
    html_content = img_regex.sub(replace_src, message)
    msg_alt.attach(MIMEText(html_content, "html", "utf-8"))

    for part in attachments:
        msg_root.attach(part)

    # 4) Відправка з TLS і коректним хендлінгом помилок
    try:
        if _CERT_PATH:
            ctx = ssl.create_default_context(cafile=_CERT_PATH)
        else:
            # фолбек: системні сертифікати
            ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(from_email, password)
            server.send_message(msg_root)
        print("Email sent successfully.")
        return True

    except smtplib.SMTPRecipientsRefused as e:
        # Напр., 550 5.1.1 – «No such user»
        reason = e.recipients.get(to_email)
        print(f"Recipient refused: {to_email} -> {reason}")
        return False

    except smtplib.SMTPDataError as e:
        # 5xx – перманентно, 4xx – тимчасово
        print(f"SMTP DATA error: {getattr(e, 'smtp_code', '?')} {getattr(e, 'smtp_error', b'').decode(errors='ignore')}")
        return False

    except Exception as e:
        print("Email send error:", str(e))
        return False

def build_telegram_message(booking_data, appointments):
    client = booking_data.get("client", {})
    message_lines = [
        "✅ Нове бронювання!",
        "",
        f"Клієнт: {client.get('name', 'Невідомо')}",
        f"Телефон: {client.get('phone', 'Невідомо')}",
        f"Email: {client.get('email', 'Невідомо')}",
        "",
        "Послуги:"
    ]
    
    # Проходимося по кожній послузі
    for svc in booking_data.get("services", []):
        try:
            # Припускаємо, що дата і час послуги передаються в полях 'date' і 'time'
            dt = datetime.strptime(f"{svc.get('date')} {svc.get('time')}", "%Y-%m-%d %H:%M")
            duration = int(svc.get("duration", 0))
            service_name = svc.get("serviceName", "Услуга")
            # Використовуємо значення, отримане від служби або hardcoded, якщо потрібно
            # Наприклад, якщо майстер передається, використаємо його, інакше – інше значення
            master = svc.get("employeeName", "Майстер не визначено")
            
            # Форматуємо час для відображення (наприклад, 07.03.25 / 14:30)
            date_display = dt.strftime("%d.%m.%y")
            time_display = dt.strftime("%H:%M")
            
            # Формуємо повідомлення для поточної послуги
            message_lines.append(
                f"- {service_name}, {svc.get('date')} {svc.get('time')}, тривалість: {duration} хв."
            )
            message_lines.append(f"Майстер: {master}")
            message_lines.append(f"Дата/час: {date_display} / {time_display}")
        except Exception as e:
            # Якщо виникає помилка при обробці даних для послуги, пропускаємо її
            continue
    
    return "\n".join(message_lines)

def send_to_telegram(message, *, token, chat_id):
    """Send ``message`` to Telegram using provided credentials."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        # краще відправляти як JSON, щоб уникнути нюансів з кодуванням
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.RequestException as e:
        print("HTTP error sending to Telegram:", e)
        return {"ok": False, "description": str(e)}
    except ValueError:
        print("Не вдалося розпарсити відповідь від Telegram")
        return {"ok": False, "description": "Invalid JSON response"}

    if not result.get("ok"):
        print("Telegram API error:", result.get("description"))
    return result

def render_service_email_html(booking_data, *, base_url="https://booklondon.p-de-p.com"):
    """Render email1.html for all services in ``booking_data``.

    The hero image and preheader are taken from the first service.  For each
    selected service a block with appointment details and the "Як підготуватись"
    tips is generated.  If there are multiple services, these blocks are simply
    concatenated one after another.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    services = booking_data.get("services", [])
    if not services:
        return ""
    lang = booking_data.get("lang", "ua").lower()

    # --- Email UI translations ------------------------------------------------
    EMAIL_I18N = {
        "ua": {
            "title": "Підтвердження візиту",
            "preheader": "Ваш візит заброньовано. Чекаємо на вас {date}. {service}.",
            "intro": "Ваш візит заброньовано, та ми з нетерпінням чекаємо нашої зустрічі. Наш адміністратор у разі потреби сконтактує з вами за вибраним варіантом взаємодії, для уточнення деталей.",
            "date_time": "Дата та час",
            "service": "Послуга",
            "location": "Локація",
            "master": "Майстер",
            "get_directions": "Прокласти маршрут",
            "add_to_calendar": "Додати в календар",
            "how_to_prepare": "Як підготуватись до візиту?",
            "default_name": "Клієнт",
            "cal_description": "Візит заброньовано з {master}",
            "footer_services": "ПОСЛУГИ",
            "footer_salons": "САЛОНИ",
            "footer_certificates": "СЕРТИФІКАТИ",
        },
        "en": {
            "title": "Booking confirmation",
            "preheader": "Your visit is booked. We are waiting for you on {date}. {service}.",
            "intro": "Your visit is booked and we look forward to seeing you. Our administrator will contact you if needed to clarify any details.",
            "date_time": "Date & time",
            "service": "Service",
            "location": "Location",
            "master": "Stylist",
            "get_directions": "Get directions",
            "add_to_calendar": "Add to calendar",
            "how_to_prepare": "How to prepare for your visit?",
            "default_name": "Client",
            "cal_description": "Service booked with {master}",
            "footer_services": "SERVICES",
            "footer_salons": "SALONS",
            "footer_certificates": "CERTIFICATES",
        },
        "ru": {
            "title": "Подтверждение визита",
            "preheader": "Ваш визит забронирован. Ждём вас {date}. {service}.",
            "intro": "Ваш визит забронирован, и мы с нетерпением ждём нашей встречи. Наш администратор при необходимости свяжется с вами для уточнения деталей.",
            "date_time": "Дата и время",
            "service": "Услуга",
            "location": "Локация",
            "master": "Мастер",
            "get_directions": "Проложить маршрут",
            "add_to_calendar": "Добавить в календарь",
            "how_to_prepare": "Как подготовиться к визиту?",
            "default_name": "Клиент",
            "cal_description": "Визит забронирован с {master}",
            "footer_services": "УСЛУГИ",
            "footer_salons": "САЛОНЫ",
            "footer_certificates": "СЕРТИФИКАТЫ",
        },
        "pl": {
            "title": "Potwierdzenie wizyty",
            "preheader": "Twoja wizyta jest zarezerwowana. Czekamy na Ciebie {date}. {service}.",
            "intro": "Twoja wizyta została zarezerwowana i z niecierpliwością czekamy na nasze spotkanie. Nasz administrator skontaktuje się z Tobą w razie potrzeby, aby doprecyzować szczegóły.",
            "date_time": "Data i godzina",
            "service": "Usługa",
            "location": "Lokalizacja",
            "master": "Stylista",
            "get_directions": "Wyznacz trasę",
            "add_to_calendar": "Dodaj do kalendarza",
            "how_to_prepare": "Jak przygotować się do wizyty?",
            "default_name": "Klient",
            "cal_description": "Wizyta zarezerwowana z {master}",
            "footer_services": "USŁUGI",
            "footer_salons": "SALONY",
            "footer_certificates": "CERTYFIKATY",
        },
    }
    t = EMAIL_I18N.get(lang, EMAIL_I18N["ua"])

    with open(os.path.join(base_dir, "emails", "preparation.json"), encoding="utf-8") as f:
        prep = json.load(f)

    client = booking_data.get("client", {})
    customer_name = client.get("name") or client.get("firstName") or t["default_name"]
    
    location_info = booking_data.get("location", {})
    address = location_info.get("address", {}).get("line", "")
    phone_display = location_info.get("address", {}).get("phone_display", "")
    phone_link = location_info.get("address", {}).get("phone_link", "")
    map_link = location_info.get("map_link")
    if not map_link and address:
        map_link = f"https://maps.google.com/?q={quote_plus(address)}"
    # --- Calendar link generation -----------------------------------------
    events = []
    for svc in services:
        date_str = svc.get("date")
        time_str = svc.get("time")
        try:
            duration = int(svc.get("duration", 0) or 0)
            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            events.append(
                {
                    "title": svc.get("serviceName", "Service"),
                    "start": start_dt,
                    "end": end_dt,
                    "master": svc.get("employeeName", ""),
                }
            )
        except Exception:
            continue

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%dT%H%M00Z")

    calendar_link = "#"
    if events:
        ics_content = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nCALSCALE:GREGORIAN\r\n"
        for ev in events:
            ics_content += "BEGIN:VEVENT\r\n"
            ics_content += f"SUMMARY:{ev['title']}\r\n"
            ics_content += f"DTSTART:{fmt(ev['start'])}\r\n"
            ics_content += f"DTEND:{fmt(ev['end'])}\r\n"
            ics_content += f"LOCATION:{address}\r\n"
            desc = t["cal_description"].format(master=ev['master'])
            ics_content += f"DESCRIPTION:{desc}\r\n"
            ics_content += "END:VEVENT\r\n"
        ics_content += "END:VCALENDAR"
        ics_link = "data:text/calendar;charset=utf-8," + quote(ics_content)
        gcal_link = None
        if len(events) == 1:
            ev = events[0]
            gcal_link = (
                "https://calendar.google.com/calendar/r/eventedit?"
                f"text={quote_plus(ev['title'])}"
                f"&dates={fmt(ev['start'])}/{fmt(ev['end'])}"
                f"&details={quote_plus(t['cal_description'].format(master=ev['master']))}"
                f"&location={quote_plus(address)}"
            )

        email_addr = (client.get("email") or "").lower()
        domain = email_addr.split("@")[-1] if "@" in email_addr else ""
        android_domains = {"gmail.com", "googlemail.com"}
        if gcal_link and domain in android_domains:
            calendar_link = gcal_link
        else:
            calendar_link = ics_link
    category_names = set()
    # data_dir береться з salon_info (booking.salons.data_dir) — заміна
    # старого hardcoded folder_map London-only.
    data_dir = location_info.get("data_dir") or ""
    folder = f"salons/{data_dir}" if data_dir else None
    if folder:
        try:
            with open(os.path.join(base_dir, "static", "data", folder, "categories.json"), encoding="utf-8") as f:
                categories_list = json.load(f)
            for cat in categories_list:
                raw_name = cat.get("name", "").upper()
                match = re.search(r"EN\s+([^/]+)", raw_name)
                if match:
                    raw_name = match.group(1)
                clean_name = re.sub(r"[^A-Z]", "_", raw_name).strip("_")
                category_names.add(clean_name)
        except Exception:
            pass
    def resolve_category_name(svc: dict) -> str:
        raw = (svc.get("parent") or svc.get("category") or "").upper()
        match = re.search(r"EN\s+([^/]+)", raw)
        if match:
            raw = match.group(1)
        clean = re.sub(r"[^A-Z]", "_", raw).strip("_")
        
        alias_map = [
            ("MACHINE_MASSAGE", ["ICOONE", "STRATOSPHERE", "MACHINE_MASSAGE"]),
            ("MASSAGE", ["MASSAGE"]),
            ("HAIR_REMOVAL", ["EPIL", "DEPIL", "WAX", "SUGAR", "HAIR_REMOVAL"]),
            ("MALE_SERVICES", ["MEN", "MALE"]),
            (
                "HAIR",
                ["HAIR", "BLOW", "COLOR", "HIGHLIGHT", "PERM", "TREATMENT", "STYLING", "CUT"],
            ),
            (
                "COSMETOLOGY",
                ["COSMET", "BROW", "LASH", "MAKEUP", "MAKE_UP", "VISAGE", "FACE"],
            ),
            ("NAILS", ["NAIL", "MANIC", "PEDIC"]),
        ]
        for canonical, patterns in alias_map:
            for pattern in patterns:
                if pattern in clean:
                    return canonical
        if category_names and clean not in category_names:
                return ""
        return clean
    images = {
        "NAILS": "./imgs/nails.png",
        "COSMETOLOGY": "./imgs/cosmetology.png",
        "HAIR": "./imgs/hair.png",
        "MALE_SERVICES": "./imgs/male-services.png",
        "HAIR_REMOVAL": "./imgs/visage.png",
        "MASSAGE": "./imgs/massage.png",
        "MACHINE_MASSAGE": "./imgs/IcooneAndStratosphere.png",
    }
    def format_datetime(svc):
        date_str = svc.get("date", "")
        time_str = svc.get("time", "")
        duration = int(svc.get("duration", 0) or 0)
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            end_dt = dt + timedelta(minutes=duration)
            return f"{dt.strftime('%d %B')}, {dt.strftime('%H:%M')}—{end_dt.strftime('%H:%M')}"
        except Exception:
            return f"{date_str} {time_str}"

    first_service = services[0]
    first_category = resolve_category_name(first_service)
    # image береться з salon_info (booking.salons.image). data-info.json лукап
    # видалено — він був тільки для старих London salon1/salon2 шляхів.
    location_image = location_info.get("image")
    default_hero = f"/static/{location_image}" if location_image else "./imgs/header-email.png"
    hero_image = images.get(first_category, default_hero)
    first_date_display = format_datetime(first_service)
    first_service_name = first_service.get("serviceName", "")
    preheader = t["preheader"].format(date=first_date_display, service=first_service_name)

    service_blocks = []
    for idx, svc in enumerate(services):
        category = resolve_category_name(svc)
        tips = prep.get(category, {}).get(lang, [])
        tips_html = "".join(
            f'<div style="padding:{"0 0 10px 0" if i==0 else "10px 0"};font-size: 18px;line-height: 1.5;font-weight: 400;color: #010202;"><strong>{i+1}/</strong> {tip}</div>'
            for i, tip in enumerate(tips)
        )

        date_display = format_datetime(svc)
        service_name = svc.get("serviceName", "")
        master_name = svc.get("employeeName", "")

        actions_html = ""
        if idx == 0:
            actions_html = f"""
            <!-- ACTIONS (2 outline + 1 primary синя) -->
            <tr class=\"actions\">
              <td class=\"px\" style=\"padding:0 32px;\">
                <table class=\"table-btns\" role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\">
                  <tr>
                    <!-- Маршрут -->
                    <td class=\"stack mb16\" align=\"center\" style=\"padding:0 15px 0 0;\">
                      <a class=\"btn btn-outline\" href=\"{map_link or ''}\"
                         style=\"font-family:Arial, Helvetica, sans-serif;background:#ffffff;border:1px solid #111111;color:#111111;text-decoration:none;display:block;width:100%;padding:14px 0;line-height:1.3;text-align:center;font-size: 12px;text-transform: uppercase;word-break:break-word;\">
                        <img style=\"vertical-align:middle;margin-right:4px;\" src=\"./imgs/location-way.png\" alt=\"\">  {t["get_directions"]}
                      </a>
                    </td>
                    <!-- Телефон -->
                    <td class=\"stack mb16\" align=\"center\" style=\"padding:0 15px 0 15px;\">
                      <a class=\"btn btn-outline\" href=\"tel:{phone_link or ''}\"
                         style=\"font-family:Arial, Helvetica, sans-serif;background:#ffffff;border:1px solid #111111;color:#111111;text-decoration:none;display:block;width:100%;padding:14px 0;line-height:1.3;text-align:center;font-size: 12px;text-transform: uppercase;word-break:break-word;\">
                        {phone_display or ''}
                      </a>
                    </td>
                    <!-- Календар -->
                    <td class=\"stack last\" align=\"center\" style=\"padding:0 0 0 15px;\">
                      <a class=\"btn btn-primary\" href=\"{calendar_link}\"
                         style=\"font-family:Arial, Helvetica, sans-serif;background:#1073d6;color:#ffffff;text-decoration:none;display:block;width:100%;padding:14px 0;line-height:1.3;text-align:center;font-size: 12px;text-transform: uppercase;word-break:break-word;\">
                        {t["add_to_calendar"]}
                      </a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
        """
        prepare_block = ""
        if tips_html:
            prepare_block = f"""            <!-- HOW TO PREPARE (ліва колонка - заголовок, права - список) -->
            <tr>
              <td class=\"px center-sm\" style=\"padding:90px 32px 90px 32px;\">
                <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\">
                  <tr>
                    <td class=\"col-label stack\" align=\"center\" style=\"vertical-align:top;padding:4px 12px 0 0;\">
                        <div class=\"center-sm\" style=\"font-family:Arial, Helvetica, sans-serif;font-size:15px;color:#010202;text-transform:uppercase;letter-spacing:-1px;line-height: 1.5;font-weight: 600;\">
                        {t["how_to_prepare"]}
                      </div>
                    </td>
                    <td class=\"col-list stack\" align=\"center\" style=\"vertical-align:top;\">
                        <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"background:#ffffff;\">
                          <tr>
                            <td align=\"center\">
                              <div class=\"center-sm\" style=\"font-family:Arial, Helvetica, sans-serif;font-size:15px;line-height:1.7;color:#2a2a2a;\">{tips_html}</div>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""
        block = f"""
            <!-- CARD: DETAILS (desktop = 2 cols, mobile = stack, hybrid + MSO) -->
            <tr>
              <td class=\"px\" style=\"padding:45px 32px;\">
                <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"border:1px solid #dcdcdc;background:#ffffff;\">
                  <!-- Ряд 1 -->
                  <tr>
                    <td style=\"padding:0;font-size:0;line-height:0;\">
                      <!--[if mso]><table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" width=\"100%\"><tr><td width=\"300\" valign=\"top\"><![endif]-->
                      <div style=\"display:inline-block;vertical-align:top;width:100%;max-width:290px;box-sizing:border-box;
                                  padding:16px 18px;border-bottom:1px solid #e7e7e7;word-break:break-word;overflow-wrap:anywhere;white-space:normal;\">
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#00000099;line-height:1.5;text-transform:uppercase;letter-spacing:1px;\">
                          {t["date_time"]}
                        </div>
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.3;color:#000;padding:5px 0;font-weight:500;\">
                          {date_display}
                        </div>
                      </div>
                      <!--[if mso]></td><td width=\"300\" valign=\"top\"><![endif]-->
                      <div style=\"display:inline-block;vertical-align:top;width:100%;max-width:290px;box-sizing:border-box;
                                  padding:16px 18px;border-bottom:1px solid #e7e7e7;border-left:1px solid #ededed;
                                  word-break:break-word;overflow-wrap:anywhere;white-space:normal;\">
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#00000099;line-height:1.5;text-transform:uppercase;letter-spacing:1px;\">
                          {t["service"]}
                        </div>
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.3;color:#000;padding:5px 0;font-weight:500;\">
                          {service_name}
                        </div>
                      </div>
                      <!--[if mso]></td></tr></table><![endif]-->
                    </td>
                  </tr>

                  <!-- Ряд 2 -->
                  <tr>
                    <td style=\"padding:0;font-size:0;line-height:0;\">
                      <!--[if mso]><table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" width=\"100%\"><tr><td width=\"300\" valign=\"top\"><![endif]-->
                      <div style=\"display:inline-block;vertical-align:top;width:100%;max-width:290px;box-sizing:border-box;
                                  padding:16px 18px;word-break:break-word;overflow-wrap:anywhere;white-space:normal;\">
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#00000099;line-height:1.5;text-transform:uppercase;letter-spacing:1px;\">
                          {t["location"]}
                        </div>
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.3;color:#000;padding:5px 0;font-weight:500;\">
                          {address}
                        </div>
                      </div>
                      <!--[if mso]></td><td width=\"300\" valign=\"top\"><![endif]-->
                      <div style=\"display:inline-block;vertical-align:top;width:100%;max-width:290px;box-sizing:border-box;
                                  padding:16px 18px;border-left:1px solid #ededed;
                                  word-break:break-word;overflow-wrap:anywhere;white-space:normal;\">
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#00000099;line-height:1.5;text-transform:uppercase;letter-spacing:1px;\">
                          {t["master"]}
                        </div>
                        <div style=\"font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.3;color:#000;padding:5px 0;font-weight:500;\">
                          {master_name}
                        </div>
                      </div>
                      <!--[if mso]></td></tr></table><![endif]-->
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            {actions_html}
            {prepare_block}
        """
        block = block.replace("{{prepare_block}}", prepare_block)
        service_blocks.append(block)

    service_sections_html = "".join(service_blocks)
    template_path = os.path.join(base_dir, "emails", "email1.html")
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    
    html = (
        html.replace("{{preheader}}", preheader)
            .replace("{{hero_image}}", hero_image)
            .replace("{{client_name}}", customer_name)
            .replace("{{service_sections}}", service_sections_html)
            .replace("{{email_title}}", t["title"])
            .replace("{{email_intro}}", t["intro"])
            .replace("{{footer_services}}", t["footer_services"])
            .replace("{{footer_salons}}", t["footer_salons"])
            .replace("{{footer_certificates}}", t["footer_certificates"])
    )
    links = location_info.get("menu_links", {})
    html = (
        html.replace("{{link_services}}", links.get("services", "#"))
            .replace("{{link_salons}}", links.get("salons", "#"))
            .replace("{{link_certificates}}", links.get("certificates", "#"))
            .replace("{{link_instagram}}", links.get("instagram", "#"))
            .replace("{{link_telegram_bot}}", links.get("telegram_bot", "#"))
            .replace("{{link_facebook}}", links.get("facebook", "#"))
    )
    return html

def render_success_email_html(booking_data, *, base_url="https://booklondon.p-de-p.com", show_buttons=False):
    """Render the success page HTML for email notification.

    Parameters
    ----------
    booking_data: dict
        Data describing the booking.
    base_url: str, optional
        Base URL used to build absolute paths for static assets in the email.
    show_buttons: bool, optional
        Whether to include navigation/calendar buttons. Defaults to ``False`` for
        emails.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    lang = booking_data.get("lang", "EN").upper()
    translations_map = {"EN": "en.json", "RU": "ru.json", "UA": "ua.json", "PL": "pl.json"}
    translations_file = translations_map.get(lang, "ua.json")
    with open(os.path.join(base_dir, "static", "data", "translations", translations_file), encoding="utf-8") as f:
        translations = json.load(f)[0]

    client_info = booking_data.get("client", {})
    customerName = client_info.get("name") or client_info.get("firstName") or "Клієнт"

    servicesSummary = ""
    cartData = []
    for svc in booking_data.get("services", []):
        specialist = {
            "fullName": svc.get("employeeName", ""),
            "specialization": svc.get("positionNames", "")
        }
        service_name = svc.get("serviceName", "Услуга")
        try:
            dt_start = datetime.strptime(f"{svc.get('date')} {svc.get('time')}", "%Y-%m-%d %H:%M")
            duration = int(svc.get("duration", 0))
            date_str_display = dt_start.strftime("%d %B")
            time_str_display = dt_start.strftime("%H:%M")
            date_str = svc.get("date", "")
            time_str = svc.get("time", "")
        except Exception:
            date_str_display = time_str_display = ""
            date_str = time_str = ""
            duration = ""

        price_amount = svc.get("price", "")
        price_currency = svc.get("currency", "")

        addServiceIcon = f"{base_url}/static/img/email/addServiceIcon.png"
        avatar = f"{base_url}/static/img/avatar.png"
        dateTimeServiceIcon = f"{base_url}/static/img/email/dateTimeServiceIcon.png"
        priceServiceIcon = f"{base_url}/static/img/email/priceServiceIcon.png"

        servicesSummary += f"""
        <table 
            width="100%" 
            cellpadding="0" 
            cellspacing="0" 
            align="center" 
            style="border-collapse:collapse; margin:20px 0; font-family:Arial,sans-serif;"
        >
        <tr>
            <td 
                width="40"
                align="center"
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05);"
            >
            <img src="{addServiceIcon}" alt="Service Icon">
            </td>
            <td 
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05); vertical-align: middle;"
            >
            <span style="font-weight:bold; margin-right:8px;">{service_name}</span>
            <span>{duration} {translations["indDurationService"]}</span>
            </td>
        </tr>
        <tr>
            <td colspan="2" height="8" style="font-size:8px; line-height:8px;">&nbsp;</td>
        </tr>
        <tr>
            <td 
                width="40"
                align="center"
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05);"
            >
            <img src="{avatar}" alt="Master Photo" style="width:40px;height:40px;border-radius:50%;">
            </td>
            <td 
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05); vertical-align: middle;"
            >
            <span style="font-weight:bold;">{specialist['fullName']}</span>
            <span style="margin-left:8px;">{specialist['specialization']}</span>
            </td>
        </tr>
        <tr>
            <td colspan="2" height="8" style="font-size:8px; line-height:8px;">&nbsp;</td>
        </tr>
        <tr>
            <td 
                width="40"
                align="center"
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05);"
            >
            <img src="{dateTimeServiceIcon}" alt="DateTime Icon">
            </td>
            <td 
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05); vertical-align: middle;"
            >
            <span style="margin-right:8px;">{date_str_display}</span>
            <span>{time_str_display}</span>
            </td>
        </tr>
        <tr>
            <td colspan="2" height="8" style="font-size:8px; line-height:8px;">&nbsp;</td>
        </tr>
        <tr>
            <td 
                width="40"
                align="center"
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05);"
            >
            <img style="
                width:20px;
                height:20px;
                padding:10px;
                background:#fff;
                border-radius:50%;
                display:block;" class="priceserviceicon" src="{priceServiceIcon}" alt="Price Icon">
            </td>
            <td 
                valign="middle"
                style="padding:14px; background:rgba(0,0,0,0.05); vertical-align: middle;"
            >
            <span style="margin-right:4px;">Вартість:</span>
            <span>{price_amount} {price_currency}</span>
            </td>
        </tr>
        </table>
        """



        cartData.append({
            "date": date_str,
            "time": time_str,
            "duration": str(duration),
            "name": service_name,
            "master": specialist["fullName"]
        })

    env = Environment(loader=FileSystemLoader(os.path.join(base_dir, "templates")))
    env.globals['url_for'] = (
        lambda endpoint, filename: f"{base_url}/static/{filename}"
        if endpoint == 'static' else ''
    )
     # Use a dedicated template for email notifications so that images and
    # layout are optimised for email clients.
    template = env.get_template("email.html")
    return template.render(customerName=customerName,
                          servicesSummary=servicesSummary,
                          cartData=cartData,
                          translations=translations,
                          lang=lang,
                          show_buttons=show_buttons) 

# ============================
def normalize_phone(phone: str) -> str:
    """Нормалізувати номер телефону - прибрати всі символи крім цифр."""
    if not phone:
        return ""
    return ''.join(filter(str.isdigit, phone))

def find_client_in_crm(client_data, headers):
    """Знайти клієнта в CRM по телефону або email."""
    url_clients = "https://api.aihelps.com/v1/clients?fields=name%2Cphone%2Cemail"
    response_clients = requests.get(url_clients, headers=headers)
    response_clients.raise_for_status()
    clients_data = response_clients.json()
    
    client_phone = client_data.get("phone", "")
    client_email = client_data.get("email", "").lower()
    
    if isinstance(clients_data, list):
        for client in clients_data:
            # Перевіряємо телефон
            phone_list = client.get("phone")
            if phone_list and isinstance(phone_list, list):
                for phone_value in phone_list:
                    normalized_crm_phone = normalize_phone(phone_value)
                    normalized_client_phone = normalize_phone(client_phone)
                    
                    if normalized_crm_phone == normalized_client_phone:
                        return client
            
            # Перевіряємо email
            email_list = client.get("email")
            if email_list and isinstance(email_list, list):
                for email_value in email_list:
                    if email_value.lower() == client_email:
                        return client
    
    return None

def _clean_text(s):
    """Очищає рядок від зайвих пробілів"""
    return s.strip() if isinstance(s, str) else s

def _clean_phone(raw):
    """Видаляє зайві символи, залишає тільки + і цифри"""
    if not raw:
        return ""
    raw = raw.strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+"):
        phone = "+" + digits
    elif digits:
        phone = "+" + digits
    else:
        phone = ""
    return phone

def create_client_in_crm(client_data, headers, location_id, max_retries=5, initial_delay=5):
    """
    Створює клієнта в CRM з повторними спробами та exponential backoff.

    Args:
        client_data: дані клієнта
        headers: заголовки для API
        location_id: ID локації
        max_retries: максимальна кількість спроб (default: 5)
        initial_delay: початкова затримка між спробами в секундах (default: 5)

    Затримки між спробами: 5s, 10s, 20s, 40s (exponential backoff)
    """
    full_name = _clean_text(client_data.get("name") or "")
    firstname, lastname = (full_name.split(" ", 1) + [""])[:2]
    firstname, lastname = _clean_text(firstname), _clean_text(lastname)

    phone = _clean_phone(client_data.get("phone"))
    email = _clean_text(client_data.get("email"))

    payload = {
        "firstname": firstname,
        "lastname": lastname,
        "location": location_id
    }

    if phone:
        payload["phone"] = [phone]
    if email:
        payload["email"] = [email]

    print(f"🧾 Підготовлено дані клієнта для створення: {json.dumps(payload, ensure_ascii=False)}")

    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔁 Спроба {attempt}/{max_retries} створення клієнта...")
            r = requests.post("https://api.aihelps.com/v1/clients",
                              headers=headers, json=payload, timeout=30)
            if r.status_code < 400:
                data = r.json()
                print(f"✅ Клієнта створено: {data.get('id')}")
                return data
            else:
                print(f"⚠️ Відповідь {r.status_code}: {r.text}")
        except requests.exceptions.Timeout:
            print(f"❌ Timeout при створенні клієнта (спроба {attempt})")
        except requests.exceptions.ConnectionError:
            print(f"❌ Помилка з'єднання при створенні клієнта (спроба {attempt})")
        except Exception as e:
            print(f"❌ Виняток при створенні клієнта: {e}")

        if attempt < max_retries:
            print(f"⏳ Очікування {delay} секунд перед наступною спробою...")
            time.sleep(delay)
            delay = min(delay * 2, 60)  # exponential backoff, max 60s

    print(f"❌ Не вдалося створити клієнта після {max_retries} спроб.")
    return None

def get_booking_data_by_reference(reference):
    """Отримати дані бронювання з БД по reference."""
    try:
        connection = get_db()
        cursor = connection.cursor()

        sql = "SELECT client_info, appointments FROM appointmentsleads WHERE reference = %s"
        cursor.execute(sql, (reference,))
        result = cursor.fetchone()

        cursor.close()
        connection.close()

        if result:
            client_info_raw, appointments_raw = result
            # psycopg2 повертає JSONB як dict/list напряму
            client_info = (json.loads(client_info_raw)
                           if isinstance(client_info_raw, (str, bytes, bytearray))
                           else client_info_raw)
            appointments = (json.loads(appointments_raw)
                            if isinstance(appointments_raw, (str, bytes, bytearray))
                            else appointments_raw)
            
            # Конвертуємо appointments в формат services для сумісності
            services = []
            for appointment in appointments:
                for service in appointment.get("services", []):
                    start_time = appointment.get("startTime", "")
                    if start_time:
                        # Парсимо ISO формат
                        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        date_str = dt.strftime("%Y-%m-%d")
                        time_str = dt.strftime("%H:%M")
                    else:
                        date_str = time_str = ""
                    
                    # Обчислюємо тривалість
                    end_time = appointment.get("endTime", "")
                    duration = 0
                    if start_time and end_time:
                        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        duration = int((end_dt - start_dt).total_seconds() / 60)
                    
                    service_data = {
                        "serviceName": service.get("name", ""),
                        "serviceId": "",  # Потрібно буде знайти в CRM
                        "category": service.get("category", ""),
                        "duration": str(duration),
                        "price": str(service.get("price", {}).get("amount", 0)),
                        "currency": service.get("price", {}).get("currency", "GBP"),
                        "employeeId": appointment.get("specialist", {}).get("id", ""),
                        "employeeName": appointment.get("specialist", {}).get("fullName", ""),
                        "positionNames": appointment.get("specialist", {}).get("specialization", ""),
                        "employeePhone": appointment.get("specialist", {}).get("phoneNumber", ""),
                        "date": date_str,
                        "time": time_str,
                        "description": appointment.get("comment", "")
                    }
                    services.append(service_data)
            
            return {
                "client": client_info,
                "services": services
            }
        
        return None
        
    except Exception as e:
        print(f"Error getting booking data: {str(e)}")
        return None

# ============================
# Функция для создания заявки через внешний API с созданием клиента
def create_appointment_service(booking_data, *, database_code, skip_state_check=False):
    print("=== СТВОРЕННЯ APPOINTMENT З ПЕРЕВІРКОЮ КЛІЄНТА ===")
    print(f"Booking data: {json.dumps(booking_data, indent=2, ensure_ascii=False)}")

    try:
        # Якщо передано тільки reference, отримуємо дані з БД
        if isinstance(booking_data, str):
            reference = booking_data
            booking_data = get_booking_data_by_reference(reference)
            if not booking_data:
                return {"error": f"Не знайдено дані для reference: {reference}"}, 400
        else:
            reference = booking_data.get("reference")

        # Атомарна перевірка та блокування: намагаємося змінити стан planned -> processing
        # Це запобігає race condition коли кілька потоків одночасно намагаються створити appointment
        # skip_state_check=True якщо викликається з _check_payment_status (стан вже processing)
        if reference and not skip_state_check:
            try:
                connection = get_db()
                cursor = connection.cursor()

                # Атомарне оновлення: тільки якщо стан = "planned", змінюємо на "processing"
                cursor.execute(
                    "UPDATE appointmentsleads SET actual_state='processing' WHERE reference=%s AND actual_state='planned'",
                    (reference,)
                )
                rows_affected = cursor.rowcount
                connection.commit()

                if rows_affected == 0:
                    # Перевіряємо поточний стан
                    cursor.execute(
                        "SELECT actual_state FROM appointmentsleads WHERE reference=%s",
                        (reference,)
                    )
                    result = cursor.fetchone()
                    current_state = result[0] if result else None
                    cursor.close()
                    connection.close()

                    if current_state in ("created", "processing"):
                        print(f"Appointment для {reference} вже {'створено' if current_state == 'created' else 'в процесі створення'}, пропускаємо")
                        return {"message": f"Appointment already {current_state}"}, 200
                    elif current_state is None:
                        print(f"Reference {reference} не знайдено в базі")
                        return {"error": "Reference not found"}, 404

                cursor.close()
                connection.close()
                print(f"✅ Заблоковано reference {reference} для створення appointment (стан: processing)")

            except Exception as db_err:
                print(f"Error checking/locking appointment state: {db_err}")
        
        client_data = booking_data.get("client", {})
        services = booking_data.get("services", [])
        
        if not services:
            return {"error": "Нет услуг в запросе"}, 400

        # 1) Получаем access_token
        url_token = "https://api.aihelps.com/v1/auth/database"
        params_token = {
            "application_id": os.getenv('AIHELPS_APPLICATION_ID'),
            "application_secret": os.getenv('AIHELPS_APPLICATION_SECRET'),
            "database_code": database_code,
            "location": os.getenv('AIHELPS_LOCATION')
        }
        response_token = requests.get(url_token, params=params_token)
        response_token.raise_for_status()
        access_token = response_token.json()["access_token"]
        print("✅ Access token получен")
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # 2) Получаем локацию (id) через API
        url_locations = "https://api.aihelps.com/v1/locations?fields=name"
        response_locations = requests.get(url_locations, headers=headers)
        response_locations.raise_for_status()
        locations_data = response_locations.json()
        if isinstance(locations_data, list) and len(locations_data) > 0:
            location_id = locations_data[0]["id"]
            print(f"✅ Используется локация с id: {location_id}")
        else:
            raise ValueError("Не удалось получить список локаций или список пуст.")
        
        # 3) Поиск или создание клиента
        client_found = find_client_in_crm(client_data, headers)
        created_client_now = False  # NEW: прапорець "клієнта щойно створили"

        if client_found:
            client_id = client_found["id"]
            client_name = client_found.get("name", client_data.get("name"))
            print(f"✅ Клиент найден: {client_name} (ID: {client_id})")
        else:
            print("⚠️  Клиент не найден, создаем нового...")
            new_client = create_client_in_crm(client_data, headers, location_id)
            if new_client:
                client_id = new_client["id"]
                client_name = new_client.get("name", client_data.get("name"))
                created_client_now = True  # NEW: щойно створили
                print(f"✅ Новый клиент создан: {client_name} (ID: {client_id})")
            else:
                print("❌ Не удалось создать клиента, создаем appointment без клиента")
                client_id = None
        
        # 4) Створення окремого appointment для КОЖНОЇ послуги
        url_create_appointment = "https://api.aihelps.com/v1/appointments"
        params_create = {"force": "true"}

        # Якщо клієнта щойно створили — зачекати для синхронізації в CRM
        if created_client_now:
            print("⏳ Затримка 10 секунд після створення клієнта перед створенням appointment...")
            time.sleep(10)

        all_results = []
        last_error_text = None
        total_services = len(services)

        for service_index, service in enumerate(services, 1):
            date_str = service.get("date", "")
            time_str = service.get("time", "")

            service_item = {
                "start": f"{time_str}:00.000Z",
                "duration": int(service.get("duration", 0)),
                "price": float(service.get("price", 0)),
                "quantity": 1,
                "professional": service.get("employeeId"),
                "service": service.get("serviceId")
            }

            # Формування даних для створення окремого Appointment
            data_appointment = {
                "location": location_id,
                "date": date_str,
                "services": [service_item]  # Одна послуга на один appointment
            }

            # Додаємо клієнта якщо знайшли/створили
            if client_id:
                data_appointment["client"] = client_id
            else:
                # Якщо не вдалося створити клієнта, додаємо дані в коментарі
                client_comment = (
                    f"Ім'я клієнта: {client_data.get('name', '')}, "
                    f"телефон: {client_data.get('phone', '')}, "
                    f"email: {client_data.get('email', '')}"
                )
                data_appointment["groupComments"] = [{"text": client_comment}]

            print(f"📋 [{service_index}/{total_services}] Дані для створення appointment: {json.dumps(data_appointment, indent=2, ensure_ascii=False)}")

            # Retry логіка з exponential backoff: 5 спроб, затримки 5s, 10s, 20s, 40s
            max_retries = 5
            initial_delay = 5
            delay = initial_delay
            result = None

            for attempt in range(1, max_retries + 1):
                try:
                    print(f"🔁 [{service_index}/{total_services}] Спроба {attempt}/{max_retries} створення appointment...")
                    response_create = requests.post(
                        url_create_appointment,
                        headers=headers,
                        params=params_create,
                        json=data_appointment,
                        timeout=30
                    )
                    if response_create.status_code < 400:
                        result = response_create.json()
                        print(f"✅ [{service_index}/{total_services}] Успішно створено Appointment!")
                        print(f"📄 Відповідь від сервера: {json.dumps(result, indent=2, ensure_ascii=False)}")
                        all_results.append(result)
                        break  # успіх -> перериваємо цикл retry
                    else:
                        last_error_text = response_create.text
                        print(f"⚠️ [{service_index}/{total_services}] Помилка {response_create.status_code}: {last_error_text}")
                except requests.exceptions.Timeout:
                    last_error_text = "Timeout"
                    print(f"❌ [{service_index}/{total_services}] Timeout при створенні appointment (спроба {attempt})")
                except requests.exceptions.ConnectionError:
                    last_error_text = "Connection error"
                    print(f"❌ [{service_index}/{total_services}] Помилка з'єднання при створенні appointment (спроба {attempt})")
                except Exception as e:
                    last_error_text = str(e)
                    print(f"❌ [{service_index}/{total_services}] Виняток при створенні appointment (спроба {attempt}): {last_error_text}")

                if attempt < max_retries:
                    print(f"⏳ Чекаємо {delay} секунд перед наступною спробою...")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)  # exponential backoff, max 60s

            # Невелика пауза між створенням різних appointments (щоб не перевантажувати API)
            if service_index < total_services:
                time.sleep(1)

        # Перевіряємо чи всі appointments створено успішно
        if len(all_results) == total_services:
            # Оновлюємо стан в БД на "created" після успішного створення
            if reference:
                try:
                    connection = get_db()
                    cursor = connection.cursor()
                    cursor.execute(
                        "UPDATE appointmentsleads SET actual_state=%s WHERE reference=%s",
                        ("created", reference)
                    )
                    connection.commit()
                    cursor.close()
                    connection.close()
                    print(f"Стан appointment оновлено на 'created' для {reference}")
                except Exception as db_err:
                    print(f"Error updating appointment state: {db_err}")
            print(f"✅ Всі {total_services} appointments успішно створено!")
            return {"appointments": all_results, "total": total_services}, 200

        # Якщо не всі appointments створено - повертаємо стан назад до "planned" для можливості повторної спроби
        if reference:
            try:
                connection = get_db()
                cursor = connection.cursor()
                cursor.execute(
                    "UPDATE appointmentsleads SET actual_state='planned' WHERE reference=%s AND actual_state='processing'",
                    (reference,)
                )
                connection.commit()
                cursor.close()
                connection.close()
                print(f"Стан appointment повернено на 'planned' для {reference} (для повторної спроби)")
            except Exception as db_err:
                print(f"Error reverting appointment state: {db_err}")

        print(f"❌ Створено лише {len(all_results)}/{total_services} appointments.")
        return {"error": last_error_text or "appointments API unavailable"}, 503

    except Exception as e:
        # При будь-якій помилці повертаємо стан назад до "planned"
        if reference:
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE appointmentsleads SET actual_state='planned' WHERE reference=%s AND actual_state='processing'",
                    (reference,)
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass
        print(f"❌ Ошибка: {str(e)}")
        return {"error": str(e)}, 500

# Если запускаем этот модуль напрямую для тестирования:
if __name__ == "__main__":
    example_booking = {
        "client": {
            "name": "Alex",
            "phone": "0000000000000",
            "email": "max@gmail.com",
            "callme": False
        },
        "services": [
            {
                "serviceName": "haircut",
                "serviceId": "88dc7bfb-c81d-ed43-02f3-a94453116140",
                "category": "Hair",
                "duration": "60",
                "price": "20.5",
                "currency": "usd",
                "employeeId": "88dc7674-7a21-d36f-109f-401954df96e9",
                "employeeName": "John Smith",
                "location_position": "hairdresser",
                "employeePhone": "+912346678",
                "date": "2025-07-13",
                "time": "08:30",
                "description": "Первый визит - стрижка"
            }
        ]
    }
    result, status = create_appointment_service(example_booking)
    print("Результат:", result)
    print("Статус:", status)




# Константы для платежной системы з .env файлу
PAYMENT_CLIENT_ID = os.getenv('PAYMENT_CLIENT_ID')
PAYMENT_CLIENT_SECRET = os.getenv('PAYMENT_CLIENT_SECRET')
PAYMENT_TOKEN_URL = "https://auth.easytip.net/realms/easytip/protocol/openid-connect/token"
# URL для проверки статуса оплаты по appointmentId
PAYMENT_STATUS_URL = "https://uk-api.easytip.net/api/v1/salons/appointments/{}"
PAYMENT_API_URL = "https://uk-api.easytip.net/api/v1/salons/appointments"
# URL для callback и редиректов – можно вынести в настройки
CALLBACK_URL = "https://test.com/callback"
SUCCESS_URL = "https://booklondon.p-de-p.com"
FAIL_URL = "https://test.com"
PAYMENT_LOCATION_ID = "2610"  

def get_payment_token(client_id, client_secret):
    """
    Получение токена платежной системы по протоколу OAuth2 (client_credentials)
    """
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        response = requests.post(PAYMENT_TOKEN_URL, data=data, headers=headers)
        response.raise_for_status()
        token_data = response.json()
        return token_data["access_token"]
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при получении платежного токена: {e}")
        return None


def call_payment_api(token, payload):
    """
    Отправка запроса на создание платежной ссылки через платежное API.
    """
    
    """
    Вызов метода для создания предоплаты (создание брони/записи)
    """
    url = PAYMENT_API_URL
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    try:
        print("Отправка запроса в платёжную систему...")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        response = requests.post(PAYMENT_API_URL, headers=headers, json=payload)
        print("Статус ответа платёжной системы:", response.status_code)
        print("Тело ответа:", response.text)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при вызове метода платежной системы: {e}")
        return None
    


def create_payment_link(booking_data, *, payment_location_id):
    try:
        # Если reference уже есть в booking_data, используем его, иначе генерируем и сохраняем
        reference = booking_data.get("reference")
        if not reference:
            reference = str(uuid.uuid4()).upper()
            booking_data["reference"] = reference

        client_data = booking_data.get("client", {})
        services = booking_data.get("services", [])
        if not services:
            return {"error": "Нет услуг в заявке."}
        
        appointments = []
        total_amount = 0.0
        for svc in services:
            date_str = svc.get("date", "")
            time_str = svc.get("time", "")
            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            duration = int(svc.get("duration", "0"))
            end_dt = start_dt + timedelta(minutes=duration)
            
            price = float(svc.get("price", 0))
            total_amount += price
            bookCurrency = svc.get("currency", "GBP").lower()
            appointment = {
                "specialist": {
                    "fullName": svc.get("employeeName") or "",
                    "specialization": svc.get("positionNames") or "Undef",
                    "id": svc.get("employeeId"),
                    "phoneNumber": svc.get("employeePhone") if svc.get("employeePhone") not in [None, "undefined"] else "000000000000"
                },
                "startTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "comment": svc.get("description", ""),
                "services": [
                    {
                        "name": svc.get("serviceName"),
                        "price": {
                            "amount": price,
                            "currency": bookCurrency
                        },
                        "count": 1
                    }
                ]
            }
            appointments.append(appointment)
        
        VAT_PERCENTAGE = 13
        total_vat = round(total_amount * VAT_PERCENTAGE / (100 + VAT_PERCENTAGE), 2)
        discount_percent = 0
        discount_amount = 0.0
        final_amount = total_amount - discount_amount
        final_vat = total_vat
        
        deposit_percent = 50
        deposit = round(final_amount * deposit_percent / 100, 2)
        deposit_vat = round(deposit * VAT_PERCENTAGE / (100 + VAT_PERCENTAGE), 2)
        
        client_info = {
            "phoneNumber": client_data.get("phone", ""),
            "firstName": client_data.get("name", ""),
            "lastName": "None",
            "email": client_data.get("email", "")
        }
        
        serviceCurrency = services[0].get("currency", "GBP").lower()
        lang = str(booking_data.get("lang", "UA") or "UA").upper()
        success_query = urlencode({"reference": reference, "lang": lang})
        success_url = f"{SUCCESS_URL.rstrip('/')}/success?{success_query}"
        payload = {
            "requireDeposit": True,
            "depositPercent": deposit_percent,
            "deposit": {
                "amount": deposit,
                "currency":  serviceCurrency
            },
            "depositVat": {
                "amount": deposit_vat,
                "currency":  serviceCurrency
            },
            "callbackUrl": CALLBACK_URL,
            "successUrl": success_url,
            "failUrl": FAIL_URL,
            "locationId": payment_location_id,
            "totalAmount": {
                "amount": total_amount,
                "currency":  serviceCurrency
            },
            "totalVat": {
                "amount": total_vat,
                "currency":  serviceCurrency
            },
            "vatPercentage": VAT_PERCENTAGE,
            "discountPercent": discount_percent,
            "discountAmount": {
                "amount": discount_amount,
                "currency":  serviceCurrency
            },
            "finalAmount": {
                "amount": final_amount,
                "currency":  serviceCurrency
            },
            "finalVat": {
                "amount": final_vat,
                "currency":  serviceCurrency
            },
            "appointments": appointments,
            "clientInfo": client_info,
            "reference": reference
        }
        
        print("Payload для платежа:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        
        token = get_payment_token(PAYMENT_CLIENT_ID, PAYMENT_CLIENT_SECRET)
        print("Токен получен:", token)
        if not token:
            return {"error": "Не удалось получить токен платежной системы."}
        
        payment_response = call_payment_api(token, payload)
        print("Ответ платежной системы:", payment_response)
        if not payment_response:
            return {"error": "Ошибка при создании платежного запроса."}
        
        payment_link = payment_response.get("paymentLink")
        appointment_id = payment_response.get("appointmentId")
        print("Получена платёжная ссылка:", payment_link)
        print("Получен appointmentId:", appointment_id)
        if payment_link:
            print("Платежная ссылка получена:", payment_link)
            result = {"status": "success", "paymentLink": payment_link}
            if appointment_id:
                result["appointmentId"] = appointment_id
            else:
                print("В ответе отсутствует appointmentId")
            return result
        else:
            print("Платёжная ссылка не получена")
            return {"error": "Платежная ссылка не получена", "response": payment_response}
    except Exception as e:
        print("Ошибка в create_payment_link:", str(e))
        return {"error": str(e)}

# ============================
# Функция мониторинга статуса оплаты
import threading
import time


def _check_payment_status(reference, appointment_id, booking_data, *, config):
    """Проверяем статус оплаты каждые 60 секунд в течение 30 минут."""
    for attempt in range(30):
        
        print(f"Проверка статуса оплаты, попытка {attempt + 1}, appointmentId={appointment_id}")
        token = get_payment_token(PAYMENT_CLIENT_ID, PAYMENT_CLIENT_SECRET)
        if not token:
            time.sleep(60)
            continue
        try:
            url = PAYMENT_STATUS_URL.format(appointment_id)
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.get(url, headers=headers, timeout=10)
            print("Статус ответа:", resp.status_code)
            print("Тело ответа:", resp.text)
            resp.raise_for_status()
            data = resp.json()
            payment_status = data.get("payment", {}).get("status")
            if payment_status and payment_status.upper() in ("PAID", "DEPOSIT_PAID", "SUCCESS","SUCCESSFUL"):
                # Атомарно оновлюємо статус оплати ТА змінюємо стан на processing
                should_create_crm = False
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    # Атомарне оновлення: тільки якщо стан ще 'planned'
                    cursor.execute(
                        "UPDATE appointmentsleads SET payment_status='paid', actual_state='processing' WHERE reference=%s AND actual_state='planned'",
                        (reference,)
                    )
                    rows_affected = cursor.rowcount
                    conn.commit()
                    cursor.close()
                    conn.close()

                    if rows_affected > 0:
                        should_create_crm = True
                        print(f"Payment confirmed for {reference}, proceeding with CRM creation")
                    else:
                        # Вже оброблюється іншим процесом або вже створено
                        print(f"Skipping CRM creation for {reference} - already processing or created")
                except Exception as db_err:
                    print(f"Error updating payment status: {db_err}")

                # Створюємо запис в CRM тільки якщо ми захопили блокування
                if should_create_crm:
                    crm_success = False
                    crm_error_code = None
                    CRM_MAX_ATTEMPTS = 3
                    CRM_RETRY_DELAY = 60  # секунд між спробами

                    for crm_attempt in range(1, CRM_MAX_ATTEMPTS + 1):
                        # Перед спробами 2+ — чекаємо і повертаємо стан на processing
                        if crm_attempt > 1:
                            print(f"CRM attempt {crm_attempt - 1} failed for {reference}. Waiting {CRM_RETRY_DELAY}s before retry...")
                            time.sleep(CRM_RETRY_DELAY)
                            try:
                                conn_retry = get_db()
                                cur_retry = conn_retry.cursor()
                                cur_retry.execute(
                                    "UPDATE appointmentsleads SET actual_state='processing' WHERE reference=%s AND actual_state='planned'",
                                    (reference,)
                                )
                                conn_retry.commit()
                                cur_retry.close()
                                conn_retry.close()
                            except Exception as db_retry_err:
                                print(f"Error resetting state for retry: {db_retry_err}")

                        try:
                            print(f"CRM attempt {crm_attempt}/{CRM_MAX_ATTEMPTS} for {reference}...")
                            crm_result, crm_status = create_appointment_service(
                                booking_data, database_code=config.get("database_code"), skip_state_check=True
                            )
                            if crm_status == 200:
                                crm_success = True
                                crm_error_code = None
                                print(f"CRM attempt {crm_attempt} succeeded for {reference}")
                                break
                            else:
                                crm_error_code = crm_status
                                print(f"CRM attempt {crm_attempt} failed with status {crm_status}: {crm_result}")
                        except Exception as crm_err:
                            crm_error_code = str(crm_err)
                            print(f"CRM attempt {crm_attempt} exception: {crm_err}")

                    # --- Telegram: формуємо повідомлення ---
                    message_lines = [
                        "⭐️ Новий самозапис",
                        "#web",
                        "<b>Сайт:</b> https://p-de-p.co.uk",
                        f"<b>Ім'я:</b> {booking_data['client'].get('name','Невідомо')}",
                        f"<b>Телефон:</b> {booking_data['client'].get('phone','Невідомо')}",
                        f"<b>Email:</b> {booking_data['client'].get('email','Невідомо')}",
                        f"<b>Клієнт зазначив:</b> {booking_data['client'].get('callme','Невідомо')}",
                        "",
                        "Послуги:"
                    ]

                    total_price = 0
                    currency = ""
                    for svc in booking_data.get("services", []):
                        service_line = (
                            f"- {svc['serviceName']} від {svc['employeeName']}, "
                            f"{svc['date']} {svc['time']}, тривалість: {svc['duration']} хв."
                        )
                        message_lines += [
                            service_line,
                            f"<b>Майстер:</b> {svc['employeeName']}",
                            f"<b>Дата/час:</b> {datetime.strptime(svc['date']+' '+svc['time'], '%Y-%m-%d %H:%M').strftime('%d.%m.%y / %H:%M')}",
                            f"<b>Вартість:</b> {svc['price']}"
                        ]
                        total_price += float(svc.get("price", 0))
                        if not currency and svc.get("currency"):
                            currency = svc["currency"]

                    message_lines += ["", f"<b>Підсумок:</b> {total_price} {currency}"]

                    # Якщо CRM не вдалось — додаємо попередження
                    if not crm_success:
                        message_lines += [
                            "",
                            f"⚠️ <b>В CRM не додано. Створіть запис.</b> Код помилки: {crm_error_code}"
                        ]

                    telegram_message = "\n".join(message_lines)
                    send_to_telegram(
                        telegram_message,
                        token=config.get("telegram_token"),
                        chat_id=config.get("telegram_chat_id")
                    )
                    try:
                        client_email = booking_data.get('client', {}).get('email')
                        if client_email and not booking_data.get("_email_sent"):
                            base_url = booking_data.get("base_url", "https://booklondon.p-de-p.com")
                            html = render_service_email_html(booking_data, base_url=base_url)
                            from_email = (config or {}).get("smtp_from_email") or None
                            if send_to_email("Booking confirmation", html, client_email, base_url=base_url, from_email=from_email):
                                booking_data["_email_sent"] = True
                    except Exception as mail_err:
                        print(f"Error sending email: {mail_err}")

                return  # Завершуємо після успішної обробки (незалежно від should_create_crm)
            else:
                print(f"Оплата не подтверждена, текущий статус: {payment_status}")
        except Exception as e:
            print(f"Error checking payment status: {e}")
        time.sleep(60)


def monitor_payment_status(reference, appointment_id, booking_data, *, config):
    thread = threading.Thread(
        target=_check_payment_status,
        args=(reference, appointment_id, booking_data),
        kwargs={"config": config},
        daemon=True
    )
    thread.start()
    return thread