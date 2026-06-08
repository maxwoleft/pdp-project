import requests
import os
from dotenv import load_dotenv

# Завантажуємо змінні з .env файлу
load_dotenv()

# Mailchimp API налаштування з .env
MAILCHIMP_API_KEY = os.getenv('MAILCHIMP_API_KEY')
MAILCHIMP_LIST_ID = os.getenv('MAILCHIMP_LIST_ID')
MAILCHIMP_SERVER = os.getenv('MAILCHIMP_SERVER', 'us22')

def add_to_mailchimp(name, email, service_categories, language=None, salon_name=None):
    """
    Додає контакт до Mailchimp з тегами категорій послуг, мови та салону
    """
    try:
        # URL для Marketing API v3.0
        url = f"https://{MAILCHIMP_SERVER}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members"
        
        # Підготовка тегів
        tags = []
        for category in service_categories:
            tags.append(category)
        
        if language:
            tags.append(f"Lang_{language}")
        
        if salon_name:
            tags.append(f"Salon_{salon_name}")
        
        # Дані для відправки
        data = {
            "email_address": email,
            "status": "subscribed",
            "merge_fields": {
                "FNAME": name
            },
            "tags": tags
        }
        
        # Аутентифікація
        auth = ("anystring", MAILCHIMP_API_KEY)
        
        # Відправка запиту
        response = requests.post(url, json=data, auth=auth, timeout=10)
        
        # Якщо контакт вже існує, оновлюємо його
        if response.status_code == 400 and "already a list member" in response.text:
            # Оновлюємо існуючий контакт
            import hashlib
            subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
            update_url = f"https://{MAILCHIMP_SERVER}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
            
            response = requests.patch(update_url, json=data, auth=auth, timeout=10)
            
            # Додаємо теги окремим запитом
            if response.status_code == 200 and tags:
                tags_url = f"https://{MAILCHIMP_SERVER}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
                tags_data = {
                    "tags": [{"name": tag, "status": "active"} for tag in tags]
                }
                requests.post(tags_url, json=tags_data, auth=auth, timeout=10)
        
        # Додаємо теги для нового контакту
        elif response.status_code in [200, 201] and tags:
            import hashlib
            subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
            tags_url = f"https://{MAILCHIMP_SERVER}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
            tags_data = {
                "tags": [{"name": tag, "status": "active"} for tag in tags]
            }
            requests.post(tags_url, json=tags_data, auth=auth, timeout=10)
        
        if response.status_code in [200, 201]:
            return {"status": "success", "message": "Contact added to Mailchimp with tags"}
        else:
            return {"status": "error", "message": f"Mailchimp error: {response.text}"}
            
    except requests.RequestException as e:
        return {"status": "error", "message": f"Mailchimp request failed: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"Exception: {str(e)}"}