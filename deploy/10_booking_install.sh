#!/usr/bin/env bash
# Install PiedDePoule Booking on droplet (fresh).
# Runs as root on droplet AFTER 08_wipe_old_booking.sh and 09_booking_deploy.sh.
#
# Idempotent — safe to re-run.

set -euo pipefail

APP_HOME=/opt/pdpbooking
APP_DIR=$APP_HOME/app
VENV=$APP_HOME/venv
LOG_DIR=/var/log/pdpbooking
ENV_FILE=$APP_DIR/.env

echo "=== [1/8] Verify prerequisites ==="
for cmd in python3 nginx systemctl; do
    command -v "$cmd" >/dev/null || { echo "missing: $cmd"; exit 1; }
done
[ -d "$APP_DIR" ] || { echo "ERROR: $APP_DIR missing — run 09_booking_deploy.sh first"; exit 1; }

echo "=== [2/8] Ensure aichat user exists (reused from AI Chat) ==="
id -u aichat >/dev/null 2>&1 || useradd -r -m -d /home/aichat -s /bin/bash aichat
chown -R aichat:aichat "$APP_HOME"

echo "=== [3/8] Log dir ==="
mkdir -p "$LOG_DIR"
chown -R aichat:aichat "$LOG_DIR"

echo "=== [4/8] Verify .env present ==="
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE missing. Copy/edit before running this script."
    echo "Minimum required keys:"
    echo "  FLASK_SECRET_KEY"
    echo "  AI_CHAT_DATABASE_URL  (postgresql://aichat:<pass>@localhost:5432/aichat)"
    echo "  SMTP_SERVER / SMTP_PORT / SMTP_FROM_NAME / SMTP_FROM_EMAIL / SMTP_PASSWORD"
    echo "  AIHELPS_APPLICATION_ID / AIHELPS_APPLICATION_SECRET / AIHELPS_LOCATION"
    echo "  PAYMENT_CLIENT_ID / PAYMENT_CLIENT_SECRET"
    echo "  MAILCHIMP_API_KEY / MAILCHIMP_LIST_ID / MAILCHIMP_SERVER"
    exit 1
fi
chmod 600 "$ENV_FILE"
chown aichat:aichat "$ENV_FILE"

echo "=== [5/8] Python venv + deps ==="
sudo -u aichat python3 -m venv "$VENV"
sudo -u aichat "$VENV/bin/pip" install --upgrade pip
sudo -u aichat "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== [6/8] systemd unit ==="
install -m 0644 "$APP_HOME/pdpbooking.service" /etc/systemd/system/pdpbooking.service
systemctl daemon-reload
systemctl enable pdpbooking.service
systemctl restart pdpbooking.service
sleep 2
systemctl --no-pager status pdpbooking.service | head -15

echo "=== [7/8] nginx vhost (HTTP only — SSL via 11_booking_ssl.sh) ==="
install -m 0644 "$APP_HOME/nginx_booking.conf" /etc/nginx/sites-available/pdpbooking
ln -sf /etc/nginx/sites-available/pdpbooking /etc/nginx/sites-enabled/pdpbooking
nginx -t
systemctl reload nginx

echo "=== [8/8] Smoke test ==="
sleep 1
curl -sf -o /dev/null -w "local HTTP %{http_code}\n" http://127.0.0.1:5050/en || echo "  ! gunicorn not responding — check journalctl -u pdpbooking -n 50"
curl -sI -H 'Host: booking.p-de-p.com' http://127.0.0.1/ | head -3 || true

echo
echo "DONE. Next: bash /opt/pdpbooking/11_booking_ssl.sh  (issues SSL via certbot)"
