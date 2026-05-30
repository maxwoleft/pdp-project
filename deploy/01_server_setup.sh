#!/usr/bin/env bash
# Перший запуск на droplet (як root).
# Встановлює: app user, swap, firewall, postgres+pgvector, redis, python, nginx, certbot.
# ВАЖЛИВО: запускати ОДИН раз. Idempotent — повторний запуск не зламає.

set -euo pipefail

APP_USER="aichat"
APP_HOME="/opt/aichat"

echo "=== [1/9] System update + base packages ==="
apt-get update
apt-get upgrade -y
apt-get install -y \
    ca-certificates curl wget gnupg lsb-release \
    build-essential git ufw fail2ban htop tmux \
    python3 python3-venv python3-pip python3-dev \
    libpq-dev pkg-config

echo "=== [2/9] Swap (2GB) — safety net for memory peaks ==="
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl vm.swappiness=10
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

echo "=== [3/9] App user ==="
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd -m -d "$APP_HOME" -s /bin/bash "$APP_USER"
fi

echo "=== [4/9] PostgreSQL 16 + pgvector ==="
# Ubuntu 24.04 ships postgresql-16
apt-get install -y postgresql-16 postgresql-contrib-16 postgresql-16-pgvector

systemctl enable --now postgresql

# Setup DB + user (idempotent через DO $$ блок)
sudo -u postgres psql <<EOF
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'aichat') THEN
      CREATE ROLE aichat LOGIN PASSWORD 'aichat_secret_CHANGE_ME';
   END IF;
END
\$\$;

SELECT 'CREATE DATABASE aichat OWNER aichat'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'aichat')\gexec
EOF

# pgvector extension у БД
sudo -u postgres psql -d aichat -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d aichat -c "GRANT ALL PRIVILEGES ON SCHEMA public TO aichat;"

echo "=== [5/9] Redis ==="
apt-get install -y redis-server
sed -i 's/^supervised .*/supervised systemd/' /etc/redis/redis.conf
systemctl enable --now redis-server

echo "=== [6/9] Nginx + Certbot ==="
apt-get install -y nginx certbot python3-certbot-nginx

echo "=== [7/9] Firewall (UFW) ==="
# Дозволяємо ssh ДО enable — щоб не заблокувати себе
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "=== [8/9] Fail2ban ==="
systemctl enable --now fail2ban

echo "=== [9/9] App directory ==="
mkdir -p "$APP_HOME"
chown -R "$APP_USER:$APP_USER" "$APP_HOME"

echo ""
echo "DONE. Next steps:"
echo "  1. Update PG password (run на droplet):"
echo "     sudo -u postgres psql -c \"ALTER USER aichat WITH PASSWORD 'YOUR_NEW_STRONG_PW';\""
echo "  2. Rsync code з локального Mac:"
echo "     ./deploy/03_deploy.sh"
echo "  3. SSH у droplet → sudo -iu aichat → запустити 02_app_install.sh"
