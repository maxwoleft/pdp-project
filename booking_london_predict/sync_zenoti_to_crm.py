import requests
import json
from typing import Dict, Any, List, Optional

# Constants
API_BASE_URL = "https://api.aihelps.com/v1"
REQUEST_TIMEOUT = 10

# Configuration
AUTH_PARAMS = {
    "application_id": "a9188d6e-b1bb-46b1-b70f-c14debefd7d7",
    "application_secret": "e60559fa-8791-471a-85ac-608b5ff9d873",
    "database_code": "776611",
    "location": "a47cbc05-5ce6-4551-9456-28ccb52bbb11"
}

class CRMSync:
    def __init__(self):
        self.access_token = self.get_access_token()
        self.crm_clients = {}
        self.phone_index = {}  # Індекс для пошуку по кінцевих цифрах
        
    def get_access_token(self) -> str:
        """Отримати токен доступу до CRM."""
        try:
            response = requests.get(
                f"{API_BASE_URL}/auth/database",
                params=AUTH_PARAMS,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()["access_token"]
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Помилка автентифікації: {str(e)}")

    def normalize_phone(self, phone: str) -> str:
        """Нормалізувати номер телефону - прибрати всі символи крім цифр."""
        if not phone:
            return ""
        return ''.join(filter(str.isdigit, phone))
    
    def find_phone_match(self, zenoti_phone: str) -> Optional[Dict]:
        """Знайти клієнта по кінцевих цифрах телефону."""
        zenoti_normalized = self.normalize_phone(zenoti_phone)
        if len(zenoti_normalized) < 7:  # Мінімум 7 цифр для пошуку
            return None
            
        for crm_phone, client in self.phone_index.items():
            # Перевіряємо, чи один номер закінчується на інший (повністю)
            if crm_phone.endswith(zenoti_normalized) or zenoti_normalized.endswith(crm_phone):
                return client
        return None

    def load_crm_clients(self) -> None:
        """Завантажити всіх клієнтів з CRM."""
        print("Завантажуємо клієнтів з CRM...")
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        try:
            response = requests.get(
                f"{API_BASE_URL}/clients",
                headers=headers,
                params={"fields": "name,phone,email,comments,archive"},
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            response_data = response.json()
            
            # API повертає масив клієнтів або об'єкт з масивом
            if isinstance(response_data, list):
                clients_data = response_data
            else:
                clients_data = response_data.get('data', response_data.get('clients', []))
            
            # Індексуємо клієнтів по телефону та email
            for client in clients_data:
                # Обробляємо телефони (можуть бути списком)
                phones = client.get("phone", [])
                if isinstance(phones, str):
                    phones = [phones]
                elif not isinstance(phones, list):
                    phones = []
                    
                for phone in phones:
                    if phone and isinstance(phone, str):
                        phone = phone.strip()
                        normalized_phone = self.normalize_phone(phone)
                        if normalized_phone:
                            self.phone_index[normalized_phone] = client
                            self.crm_clients[phone] = client
                
                # Обробляємо email (можуть бути списком)
                emails = client.get("email", [])
                if isinstance(emails, str):
                    emails = [emails]
                elif not isinstance(emails, list):
                    emails = []
                    
                for email in emails:
                    if email and isinstance(email, str):
                        email = email.strip().lower()
                        self.crm_clients[email] = client
                    
            print(f"Завантажено {len(clients_data)} клієнтів з CRM")
            print(f"Проіндексовано {len(self.phone_index)} телефонів")
            
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Помилка завантаження клієнтів: {str(e)}")

    def format_zenoti_visits(self, appointment_details: List[Dict]) -> str:
        """Форматувати дані візитів Zenoti для коментарів."""
        if not appointment_details:
            return ""
            
        comments = ["Дані візитів Zenoti:"]
        
        for i, appointment in enumerate(appointment_details, 1):
            total_price = appointment.get("total_price", 0)
            services_with_therapists = appointment.get("services_with_therapists", [])
            
            comments.append(f"• Візит {i}, загалом: ${total_price}")
            
            # Групуємо послуги по майстрах
            therapist_services = {}
            for service in services_with_therapists:
                therapist = service.get("therapist", "Майстер не вказаний")
                service_name = service.get("service", "")
                price = service.get("price", 0)
                
                if therapist not in therapist_services:
                    therapist_services[therapist] = []
                therapist_services[therapist].append(f"{service_name} (${price})")
            
            # Додаємо інформацію по кожному майстру
            for therapist, services in therapist_services.items():
                comments.append(f"  - {therapist}: {', '.join(services)}")
        
        return "\n".join(comments)

    def update_client(self, client_id: str, new_comments: str) -> bool:
        """Оновити коментарі клієнта в CRM."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        try:
            response = requests.put(
                f"{API_BASE_URL}/clients/{client_id}",
                headers=headers,
                json={"comments": new_comments},
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"Помилка оновлення клієнта {client_id}: {str(e)}")
            return False

    def format_phone_for_api(self, phone: str) -> str:
        """Форматувати телефон для API."""
        if not phone:
            return ""
        
        # Прибираємо всі символи крім цифр
        digits_only = self.normalize_phone(phone)
        
        if len(digits_only) < 10:
            return ""
        
        # Прибираємо початкові нулі
        digits_only = digits_only.lstrip('0')
        
        if len(digits_only) < 10:
            return ""
            
        # Формат як +1 888 206 20 11
        if digits_only.startswith('7') and len(digits_only) == 11:
            # Російський номер: +7 XXX XXX XX XX
            return f"+7 {digits_only[1:4]} {digits_only[4:7]} {digits_only[7:9]} {digits_only[9:11]}"
        elif len(digits_only) == 10:
            # US номер: +1 XXX XXX XX XX
            return f"+1 {digits_only[0:3]} {digits_only[3:6]} {digits_only[6:8]} {digits_only[8:10]}"
        elif len(digits_only) == 11 and not digits_only.startswith('7'):
            # Інші 11-значні номери
            return f"+{digits_only[0]} {digits_only[1:4]} {digits_only[4:7]} {digits_only[7:9]} {digits_only[9:11]}"
        else:
            # Інші номери
            return f"+{digits_only}"

    def create_client(self, zenoti_client: Dict) -> bool:
        """Створити нового клієнта в CRM."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        zenoti_comments = self.format_zenoti_visits(zenoti_client.get("appointment_details", []))
        
        # Розбиваємо ім'я на частини
        full_name = zenoti_client.get("name", "").strip()
        name_parts = full_name.split(" ", 1)
        firstname = name_parts[0] if name_parts else ""
        lastname = name_parts[1] if len(name_parts) > 1 else ""
        
        # Обробляємо телефон
        phone = zenoti_client.get("phone", "")
        if phone and not phone.startswith("+"):
            digits_only = ''.join(filter(str.isdigit, phone))
            
            # Прибираємо початкові нулі
            digits_only = digits_only.lstrip('0')
            
            # Якщо починається з 44 - це вже британський код
            if digits_only.startswith('44'):
                phone = f"+{digits_only}"
            # Якщо 10-11 цифр і починається з 7 - британський мобільний
            elif len(digits_only) in [10, 11] and digits_only.startswith('7'):
                phone = f"+44{digits_only}"
            # Інакше просто додаємо +44
            else:
                phone = f"+44{digits_only}"
        
        client_data = {
            "firstname": firstname,
            "lastname": lastname,
            "phone": [phone] if phone else [],
            "email": [zenoti_client.get("email", "")] if zenoti_client.get("email") else [],
            "comments": zenoti_comments,
            "location": "88dc7410-8542-84ac-4594-52121b277511"  # Правильний location ID
        }
        
        try:
            response = requests.post(
                f"{API_BASE_URL}/clients",
                headers=headers,
                json=client_data,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            print(f"✅ Створено нового клієнта: {full_name}")
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Помилка створення клієнта {full_name}: {str(e)}")
            print(f"Відповідь сервера: {e.response.text if hasattr(e, 'response') else 'Немає відповіді'}")
            return False

    def sync_zenoti_data(self, zenoti_file_path: str) -> None:
        """Синхронізувати дані з Zenoti файлу."""
        print("Завантажуємо дані Zenoti...")
        
        try:
            with open(zenoti_file_path, 'r', encoding='utf-8') as f:
                zenoti_clients = json.load(f)
        except FileNotFoundError:
            print(f"❌ Файл {zenoti_file_path} не знайдено")
            return
        except json.JSONDecodeError:
            print(f"❌ Помилка читання JSON файлу {zenoti_file_path}")
            return
        
        print(f"Завантажено {len(zenoti_clients)} клієнтів з Zenoti")
        
        # Завантажуємо клієнтів з CRM
        self.load_crm_clients()
        
        updated_count = 0
        created_count = 0
        failed_clients = []
        
        for zenoti_client in zenoti_clients:
            phone = zenoti_client.get("phone", "").strip()
            email = zenoti_client.get("email", "").strip().lower()
            name = zenoti_client.get("name", "")
            
            # Шукаємо клієнта в CRM по телефону або email
            crm_client = None
            
            # Спочатку шукаємо по точному співпадінню телефону
            if phone and phone in self.crm_clients:
                crm_client = self.crm_clients[phone]
            # Потім по кінцевих цифрах телефону
            elif phone:
                crm_client = self.find_phone_match(phone)
            # Нарешті по email
            elif email and email in self.crm_clients:
                crm_client = self.crm_clients[email]
            
            zenoti_comments = self.format_zenoti_visits(zenoti_client.get("appointment_details", []))
            
            if crm_client:
                # Оновлюємо існуючого клієнта
                existing_comments = crm_client.get("comments", "")
                
                # Перевіряємо, чи вже є дані Zenoti
                if "Дані візитів Zenoti:" in existing_comments:
                    print(f"⚠️  Клієнт {name} вже має дані Zenoti, пропускаємо")
                    continue
                
                # Додаємо нові коментарі до існуючих
                if existing_comments:
                    new_comments = f"{existing_comments}\n\n{zenoti_comments}"
                else:
                    new_comments = zenoti_comments
                
                if self.update_client(crm_client["id"], new_comments):
                    print(f"✅ Оновлено клієнта: {name}")
                    updated_count += 1
                    
            else:
                # Створюємо нового клієнта
                if self.create_client(zenoti_client):
                    created_count += 1
                else:
                    # Зберігаємо невдалий контакт
                    failed_clients.append(zenoti_client)
        
        # Зберігаємо невдалі контакти
        if failed_clients:
            with open("zenoti_failed_clients.json", "w", encoding="utf-8") as f:
                json.dump(failed_clients, f, ensure_ascii=False, indent=2)
            print(f"❌ Збережено {len(failed_clients)} невдалих контактів в zenoti_failed_clients.json")
        
        print(f"\n=== РЕЗУЛЬТАТ СИНХРОНІЗАЦІЇ ===")
        print(f"Оновлено клієнтів: {updated_count}")
        print(f"Створено нових клієнтів: {created_count}")
        print(f"Невдалі контакти: {len(failed_clients)}")
        print(f"Загалом оброблено: {len(zenoti_clients)} клієнтів")

def main():
    """Головна функція."""
    sync = CRMSync()
    sync.sync_zenoti_data("zenoti_detailed_services_report.json")

if __name__ == "__main__":
    main()