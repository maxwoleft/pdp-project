# Deploy AI Chat + Booking → DigitalOcean droplet

**Target**: 167.172.100.158
  * `ai-chat.p-de-p.com` — AI Chat FastAPI (uvicorn :8000)
  * `booking.p-de-p.com` — PiedDePoule Booking Flask (gunicorn :5050)

**Stack**: Ubuntu 24.04 + Postgres 16 + pgvector + Redis 7 + Python 3.12 + uvicorn/gunicorn + nginx + Let's Encrypt

## Steps (порядок!)

### 1. Server bootstrap (на droplet як root)

```bash
ssh root@167.172.100.158
# скачуємо script один раз
mkdir -p /opt/aichat
# залив 01_server_setup.sh через scp ДО першого rsync — або руками створити
nano /opt/aichat/01_server_setup.sh   # вставити вміст з deploy/01_server_setup.sh
chmod +x /opt/aichat/01_server_setup.sh
bash /opt/aichat/01_server_setup.sh
```

**Або одним рядком після rsync (краще)**: спочатку step 2, потім запустити script з `/opt/aichat/app/deploy/`.

### 2. Rsync code з Mac

```bash
# на ЛОКАЛЬНОМУ Mac
cd /Users/woleft/Documents/AI\ CHAT\ V0.1
chmod +x deploy/*.sh
bash deploy/03_deploy.sh
```

### 3. Запустити server bootstrap

```bash
ssh root@167.172.100.158
bash /opt/aichat/app/deploy/01_server_setup.sh
```

### 4. Змінити PG password (security)

```bash
# на droplet
sudo -u postgres psql -c "ALTER USER aichat WITH PASSWORD 'YOUR_STRONG_PASSWORD';"
```

### 5. Скопіювати/відредагувати .env

```bash
# на droplet
sudo -iu aichat
cp /opt/aichat/app/deploy/.env.prod.template /opt/aichat/app/.env
nano /opt/aichat/app/.env
# Замінити:
# - DATABASE_URL password (з кроку 4)
# - ADMIN_SESSION_SECRET (openssl rand -hex 32)
chmod 600 /opt/aichat/app/.env
```

### 6. Dump local DB → droplet

```bash
# на ЛОКАЛЬНОМУ Mac
bash deploy/04_dump_local_db.sh
```

### 7. App install (venv, pip, init)

```bash
# на droplet як aichat
sudo -iu aichat
bash /opt/aichat/app/deploy/02_app_install.sh
```

### 8. Seed admin user

```bash
# на droplet як aichat
cd /opt/aichat/app
source /opt/aichat/venv/bin/activate
python -m scripts.seed_admin --email admin@local --password 'YOUR_ADMIN_PW'
```

### 9. systemd + nginx + SSL

```bash
# на droplet як root
bash /opt/aichat/app/deploy/05_systemd_nginx_ssl.sh
```

### 10. Telegram webhook

```bash
# на droplet як aichat
sudo -iu aichat
bash /opt/aichat/app/deploy/06_telegram_webhook.sh
```

### 11. Cron daily sync

```bash
# на droplet як root
bash /opt/aichat/app/deploy/07_cron_sync.sh
```

## Verify

```bash
# Health check
curl -I https://ai-chat.p-de-p.com/admin/login

# Logs
journalctl -u aichat -f
tail -f /var/log/aichat/app.log

# DB
sudo -u postgres psql -d aichat -c "SELECT COUNT(*) FROM service_profile;"
```

## Updates (subsequent deploys)

```bash
# на Mac
bash deploy/03_deploy.sh

# на droplet
sudo systemctl restart aichat
```

## Booking deploy (booking.p-de-p.com)

### A. Перед першим деплоєм

* DNS: A-record `booking.p-de-p.com → 167.172.100.158` має існувати (та пройти TTL).
* AI Chat вже встановлено (Postgres крутиться, є user `aichat`).
* Postgres з міграцією booking schema:
  ```bash
  # на droplet, як aichat:
  cd /opt/aichat/app
  source /opt/aichat/venv/bin/activate
  python -m scripts.migrate_to_booking_schema
  python -m scripts.migrate_mysql_booking_data   # тільки якщо є MySQL дані
  ```

### B. Видалення старої версії booking

```bash
# На droplet як root. Бекап -> /root/booking-backup-<ts>.tgz
bash /opt/aichat/app/deploy/08_wipe_old_booking.sh
```

### C. Деплой нового коду

```bash
# На локальному Mac
bash deploy/09_booking_deploy.sh
```

### D. .env на дроплеті

```bash
# На droplet
cp /opt/aichat/app/deploy/booking.env.template /opt/pdpbooking/app/.env
nano /opt/pdpbooking/app/.env   # підстав password / secret / SMTP / Mailchimp
chmod 600 /opt/pdpbooking/app/.env
chown aichat:aichat /opt/pdpbooking/app/.env
```

### E. Інсталяція (venv + systemd + nginx)

```bash
# На droplet як root
bash /opt/pdpbooking/10_booking_install.sh
```

### F. SSL

```bash
bash /opt/pdpbooking/11_booking_ssl.sh
```

### G. Cron для JSON-синку

```bash
bash /opt/aichat/app/deploy/12_booking_cron.sh
```

### H. Перевірка

```bash
curl -I https://booking.p-de-p.com/
journalctl -u pdpbooking -f
tail -f /var/log/pdpbooking/error.log
```

### Booking updates (subsequent deploys)

```bash
# На Mac
bash deploy/09_booking_deploy.sh
# На droplet
systemctl restart pdpbooking
```

## Rollback / debug

```bash
# Стан сервісу
systemctl status aichat
journalctl -u aichat --since '10 min ago'

# Стан Postgres
sudo -u postgres psql -d aichat -c "\l"
sudo -u postgres psql -d aichat -c "SELECT name FROM service_profile LIMIT 5;"

# Перезапуск
systemctl restart aichat nginx redis-server postgresql
```
