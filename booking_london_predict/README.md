## Опис
Фреймворк Flask забезпечує веб‑сервіс для онлайн‑бронювання послуг салонів. Дані про категорії, послуги, майстрів і вільний час синхронізуються з API **AIHelps**, заявки зберігаються в **MySQL**, а платіжна частина інтегрована з платформою **EasyTip**. Додатково відправляються e‑mail та Telegram‑сповіщення.

## Структура проєкту
```
.
├── app.py                       # Основний веб‑додаток Flask і маршрути
├── appointmentService.py        # Логіка заявок, оплат, email/Telegram
├── categories.py                # Синхронізація категорій з AIHelps
├── employees.py                 # Синхронізація майстрів
├── freetime.py                  # Оновлення вільного часу майстрів
├── services.py                  # Синхронізація послуг
├── servicesThree.py             # Побудова дерева категорій/послуг
├── save_appointment_record.py   # Приклад збереження заявки через SQLAlchemy
├── emails/                      # Шаблони листів та зображення
├── templates/                   # HTML‑шаблони інтерфейсу
├── static/                      # CSS/JS, дані, шрифти, переклади
├── package.json / node_modules  # Фронтенд‑залежності
└── test_email.py                # Відправка тестового листа
```

### Ключові модулі та функції

#### `app.py`
* Завантаження даних салонів та перекладів із каталогу `static/data`.
* Глобальні фільтри cookie (`ensure_visit_cookie`, `add_visit_cookie`).
* Побудова дерева категорій (`build_category_tree`) та прив’язка послуг (`assign_services_to_categories`).
* Маршрути:
  * `/london` – сторінка бронювання для салону *l1* українською.
  * `/en`, `/ua`, `/ru` – вибір салону з перекладами.
  * `/<salon>/<lang>` – бронювання для вибраного салону та мови.
  * `/update_freetime` – перезавантаження вільного часу через `freetime.update_free_time`.
  * `/reserve_slot`, `/release_slot`, `/reserved_slots` – проста система резервування слотів у пам’яті.
  * `/<salon>/create_appointment` – створення заявки: збереження у БД (`save_appointment_record`), створення платіжної ссилки (`create_payment_link`) та фоновий моніторинг оплати (`monitor_payment_status`).

#### `appointmentService.py`
* `save_appointment_record` – запис заявки в таблицю `appointmentsleads` MySQL.
* `send_to_email` – відправка HTML‑листів із підвантаженням зображень.
* `build_telegram_message` та `send_to_telegram` – формування та відправка повідомлень.
* `render_service_email_html`/`render_success_email_html` – шаблони e‑mail.
* `create_appointment_service` – створення запису в CRM через AIHelps API.
* `get_payment_token`, `call_payment_api`, `create_payment_link` – інтеграція з EasyTip для отримання платіжної ссилки та даних депозиту.
* `monitor_payment_status` – фонове опитування статусу платежу; після підтвердження оплати викликає `create_appointment_service`, оновлює запис у БД та надсилає сповіщення.

#### Скрипти синхронізації
* `categories.py`, `employees.py`, `services.py` – отримують відповідні дані з AIHelps і зберігають JSON‑файли в `static/data/<salon>/`.
* `freetime.py` – формує `freetime.json` з доступним часом на два місяці вперед.
* `servicesThree.py` – будує дерево категорій з послугами для візуалізації.

#### Допоміжні файли
* `save_appointment_record.py` – окремий приклад збереження заявки з використанням SQLAlchemy.
* `test_email.py` – завантажує заявку з БД та відправляє тестовий e‑mail.
* Каталог `emails/` містить шаблон `email1.html`, файл порад `preparation.json` та зображення.
* Каталог `templates/` – `booking.html`, `select_salon.html`, `success.html` та базові часткові шаблони.
* Каталог `static/` –
  * `data/salon*/` – `categories.json`, `services.json`, `employees.json`, `freetime.json`, `data-info.json`.
  * `data/translations/` – переклади інтерфейсу (`en.json`, `ru.json`, `ua.json`).
  * `css`, `js`, `img`, `fonts` – ресурси інтерфейсу.

## Потік бронювання
1. Користувач обирає мову та салон – дані відображаються з локальних JSON‑файлів.
2. При створенні заявки `/create_appointment` зберігає дані в MySQL та ініціює платіж на EasyTip.
3. `monitor_payment_status` у фоновому потоці перевіряє оплату, після підтвердження створює запис у CRM, оновлює статус у БД та надсилає e‑mail/Telegram‑сповіщення.

## Запуск
### Встановлення залежностей
```bash
pip install -r requirements.txt
```

### Старт сервера
```bash
python app.py
```
Сервер буде доступний на `http://localhost:5000`.

### Telegram-бот із локальним Mistral 7B
Бот дозволяє клієнтам записуватися на послуги, спілкуючись у вільній формі в Telegram. Для роботи потрібні локальна копія
моделі **Mistral 7B Instruct** у форматі `.gguf` і токен бота.

1. Встановіть додаткові залежності:
   ```bash
   pip install -r requirements.txt
   ```
2. Підготуйте модель (наприклад, `mistral-7b-instruct-v0.2.Q4_K_M.gguf`) і вкажіть шлях до неї через змінну середовища
   `MISTRAL_MODEL_PATH`.
   Обгортка `llama-cpp-python` (над бібліотекою `llama.cpp`) за замовчуванням запускає модель повністю на CPU (`MISTRAL_GPU_LAYERS=0`), тому відеокарта не
   потрібна. За бажанням значення `TELEGRAM_BOT_TOKEN` та `MISTRAL_MODEL_PATH` можна прописати безпосередньо в `telegram_bot.py`
   у розділі "Optional in-code configuration".
3. Запустіть бота:
   ```bash
   TELEGRAM_BOT_TOKEN=<token> \
   MISTRAL_MODEL_PATH=/path/to/mistral-7b-instruct-v0.2.Q4_K_M.gguf \
   python telegram_bot.py
   ```

Додаткові змінні середовища:

* `SALON_CODE` – вибір набору даних (`salon1` або `salon2`), за замовчуванням `salon1`.
* `BOOKING_LANGUAGE` – початкова мова спілкування (`ua`, `en`, `ru`).
* `SALON_DATABASE_CODE` – перевизначення коду бази в CRM (якщо не вказано, береться зі словника `DEFAULT_SALON_DATABASE_CODES`).
* `NOTIFY_CHAT_ID` і `NOTIFY_BOT_TOKEN` – для відправки підсумкового повідомлення менеджерам.
* `MISTRAL_GPU_LAYERS` – кількість шарів у GPU (за замовчуванням `0`, щоб використовувати лише CPU).
* `MISTRAL_THREADS` – кількість CPU-потоків для декодування (за замовчуванням значення з `llama.cpp`).

Бот за допомогою локальної моделі збирає категорію, послугу, майстра, дату/час і контактні дані клієнта, перевіряє доступність
слотів за `freetime.json`, створює заявку через `save_appointment_record` і викликає `create_appointment_service` для CRM. У разі
успіху бот надсилає підтвердження клієнту та, за потреби, Telegram-сповіщення менеджерам.