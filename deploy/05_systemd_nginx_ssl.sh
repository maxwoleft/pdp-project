#!/usr/bin/env bash
# Запускається на DROPLET як root.
# Налаштовує systemd, nginx, SSL.

set -euo pipefail

APP_DIR="/opt/aichat/app"
DOMAIN="ai-chat.p-de-p.com"
EMAIL="max.woleft@gmail.com"

echo "=== [1/5] Log dir ==="
mkdir -p /var/log/aichat
chown aichat:aichat /var/log/aichat

echo "=== [2/5] systemd units ==="
cp "$APP_DIR/deploy/aichat.service" /etc/systemd/system/
# worker — поки disable (немає WorkerSettings класу)
# cp "$APP_DIR/deploy/aichat-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable aichat.service
systemctl start aichat.service
sleep 3
systemctl status aichat.service --no-pager -l | head -20

echo "=== [3/5] Nginx config ==="
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/ai-chat
ln -sf /etc/nginx/sites-available/ai-chat /etc/nginx/sites-enabled/
# Видалити default site
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "=== [4/5] Let's Encrypt SSL ==="
echo "DNS A-record для $DOMAIN має вже вказувати на цей сервер."
certbot --nginx --non-interactive --agree-tos --email "$EMAIL" -d "$DOMAIN" --redirect

echo "=== [5/5] Auto-renew cron (certbot встановив свій timer) ==="
systemctl status certbot.timer --no-pager | head -5 || true

echo ""
echo "DONE. App доступний на https://$DOMAIN"
echo "Перевірка: curl -I https://$DOMAIN/admin/login"
