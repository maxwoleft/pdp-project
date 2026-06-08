# save_appointment_record.py — writes into Postgres booking.appointmentsleads.
import uuid
from datetime import datetime, timedelta
import json
from sqlalchemy import create_engine, text

from _db import sqlalchemy_url

engine = create_engine(sqlalchemy_url(), pool_pre_ping=True)

def save_appointment_record(booking_data):
    """
    Сохраняет заявку в таблицу appointmentsleads.

    Ожидаемый формат booking_data (реальные данные подставляются из формы):
    {
        "client": {
            "name": "Имя клиента",
            "phone": "Телефон клиента",
            "email": "Email клиента",
            "callme": <boolean>   # опционально, например, false
        },
        "services": [
            {
                "serviceName": "Название услуги",
                "serviceId": "ID услуги",
                "category": "Подкатегория",
                "parent": "Головная категория",
                "duration": "60",    # длительность в минутах
                "price": "20.5",
                "currency": "usd",
                "employeeId": "ID мастера",
                "employeeName": "Имя мастера",
                "location_position": "специализация, например, hairdresser",
                "employeePhone": "Телефон мастера",
                "date": "2024-07-13",   # формат YYYY-MM-DD
                "time": "08:30",        # формат HH:MM
                "description": "Комментарий к визиту"
            },
            ... (другие услуги)
        ]
    }

    Результирующая запись в таблице будет иметь следующий вид:
    {
        "reference": "UUID",
        "actualState": "planned",
        "paymentStatus": "wait",
        "client_info": { ... },          # информация о клиенте
        "appointments": [                # массив appointment, сформированный из данных услуг
            {
                "specialist": {
                    "fullName": <employeeName>,
                    "specialization": <location_position>,
                    "id": <employeeId>,
                    "phoneNumber": <employeePhone>
                },
                "startTime": "2024-07-13T08:30:00Z",
                "endTime": "2024-07-13T09:30:00Z",
                "comment": <description>,
                "services": [
                    {
                        "name": <serviceName>,
                        "price": {
                            "amount": <price>,
                            "currency": <currency>
                        },
                        "count": 1
                    }
                ]
            },
            ... (другие appointment)
        ]
    }
    """
    try:
        # Извлекаем данные клиента и услуг из booking_data
        client = booking_data.get("client", {})
        services = booking_data.get("services", [])
        
        # Формируем объект client_info
        client_info = {
            "name": client.get("name"),
            "phone": client.get("phone"),
            "email": client.get("email"),
            "callme": client.get("callme", False)
        }
        
        # Преобразуем каждую услугу в объект appointment
        appointments = []
        categories = []
        for svc in services:
            # Вычисляем время начала записи и прибавляем длительность услуги
            start_dt = datetime.strptime(f"{svc.get('date')} {svc.get('time')}", "%Y-%m-%d %H:%M")
            duration = int(svc.get("duration", "0"))
            end_dt = start_dt + timedelta(minutes=duration)
            
            appointment = {
                "specialist": {
                    "fullName": svc.get("employeeName"),
                    "specialization": svc.get("location_position"),
                    "id": svc.get("employeeId"),
                    "phoneNumber": svc.get("employeePhone")
                },
                "startTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "comment": svc.get("description", ""),
                "services": [
                    {
                        "name": svc.get("serviceName"),
                        "price": {
                            "amount": float(svc.get("price", 0)),
                            "currency": svc.get("currency")
                        },
                        "count": 1
                    }
                ]
            }
            appointments.append(appointment)
            category_name = svc.get("parent") or svc.get("category")
            if category_name:
                categories.append(category_name)
        
         # Використовуємо reference із booking_data, щоб однакове значення
        # зберігалося в базі та передавалося у successUrl. Якщо його немає –
        # генеруємо новий та одразу підставляємо назад у booking_data.
        reference = booking_data.get("reference")
        if not reference or str(reference).lower() == "none":
            reference = str(uuid.uuid4())
        reference = str(reference).upper()
        booking_data["reference"] = reference
        
        servcategory = ", ".join(sorted(set(categories))) if categories else None
        # Собираем итоговую запись
        record = {
            "reference": reference,
            "actualState": "planned",
            "paymentStatus": "wait",
            "client_info": client_info,
            "appointments": appointments,
            "servcategory": servcategory,
        }
        
        # Преобразуем JSON-поля в строки для записи в базу данных
        client_info_str = json.dumps(client_info)
        appointments_str = json.dumps(appointments)

        with engine.begin() as conn:
            # Перевіряємо, чи вже існує запис з таким reference (захист від дублів)
            check_sql = text("SELECT reference FROM appointmentsleads WHERE reference = :reference LIMIT 1")
            existing = conn.execute(check_sql, {"reference": reference}).fetchone()

            if existing:
                # Запис вже існує — повертаємо його без створення дубля
                return {"status": "success", "reference": reference, "duplicate": True}

            # JSON columns у booking.appointmentsleads — JSONB, тому передаємо
            # рядок із приведенням до jsonb (psycopg2 не може implicit cast text→jsonb).
            sql = text("""
                INSERT INTO appointmentsleads (reference, client_info, appointments, actual_state, payment_status, servcategory)
                VALUES (:reference, CAST(:client_info AS jsonb), CAST(:appointments AS jsonb),
                        :actual_state, :payment_status, :servcategory)
            """)
            params = {
                "reference": reference,
                "client_info": client_info_str,
                "appointments": appointments_str,
                "actual_state": "planned",
                "payment_status": "wait",
                "servcategory": servcategory,
            }

            conn.execute(sql, params)

        return {"status": "success", "reference": reference}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # Пример вызова с тестовыми данными (для отладки)
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
                "serviceId": "SVC001",
                "category": "Treatment",
                "parent": "Hair",
                "duration": "60",
                "price": "20.5",
                "currency": "usd",
                "employeeId": "EMP001",
                "employeeName": "John Smith",
                "location_position": "hairdresser",
                "employeePhone": "+912346678",
                "date": "2024-07-13",
                "time": "08:30",
                "description": "Первый визит - стрижка"
            }
        ]
    }
    
    result = save_appointment_record(example_booking)
    print(result)
