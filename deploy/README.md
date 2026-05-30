# Deploy AI Chat → DigitalOcean droplet

**Target**: 167.172.100.158 (ai-chat.p-de-p.com)
**Stack**: Ubuntu 24.04 + Postgres 16 + pgvector + Redis 7 + Python 3.12 + uvicorn + nginx + Let's Encrypt

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
