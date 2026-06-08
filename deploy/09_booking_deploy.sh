#!/usr/bin/env bash
# Rsync booking_london_predict + deploy artifacts from local Mac to droplet.
# Runs on LOCAL Mac.

set -euo pipefail

DROPLET="${DROPLET:-167.172.100.158}"
APP_DIR="/opt/pdpbooking/app"
LOCAL_DIR="$(cd "$(dirname "$0")/../booking_london_predict" && pwd)"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$LOCAL_DIR" ]; then
    echo "ERROR: $LOCAL_DIR not found"
    exit 1
fi

echo "=== Ensure target dir exists ==="
ssh "root@$DROPLET" "mkdir -p $APP_DIR && chown -R aichat:aichat /opt/pdpbooking"

echo "=== Rsync booking code → root@$DROPLET:$APP_DIR ==="
rsync -avz --delete --progress \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'node_modules' \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '.env.example' \
    --exclude 'Booking_Project_Questions.xlsx' \
    --exclude 'EasyTip Platform - Integration Guide.docx' \
    "$LOCAL_DIR/" \
    "root@$DROPLET:$APP_DIR/"

echo ""
echo "=== Rsync deploy artifacts (systemd unit + nginx vhost + install scripts) ==="
rsync -avz \
    "$DEPLOY_DIR/pdpbooking.service" \
    "$DEPLOY_DIR/nginx_booking.conf" \
    "$DEPLOY_DIR/08_wipe_old_booking.sh" \
    "$DEPLOY_DIR/10_booking_install.sh" \
    "$DEPLOY_DIR/11_booking_ssl.sh" \
    "root@$DROPLET:/opt/pdpbooking/"

ssh "root@$DROPLET" "chmod +x /opt/pdpbooking/*.sh && chown -R aichat:aichat /opt/pdpbooking/app"

echo ""
echo "DONE. Next steps on droplet (as root):"
echo "  1. bash /opt/pdpbooking/08_wipe_old_booking.sh   # cleanup old"
echo "  2. nano /opt/pdpbooking/app/.env                  # set FLASK_SECRET_KEY, AI_CHAT_DATABASE_URL, etc."
echo "  3. bash /opt/pdpbooking/10_booking_install.sh     # venv + pip + systemd + nginx"
echo "  4. bash /opt/pdpbooking/11_booking_ssl.sh         # certbot for booking.p-de-p.com"
