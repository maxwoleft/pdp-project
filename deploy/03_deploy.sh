#!/usr/bin/env bash
# Rsync code з локального Mac у droplet.
# Запускається на ЛОКАЛЬНОМУ Mac.

set -euo pipefail

DROPLET="${DROPLET:-167.172.100.158}"
APP_DIR="/opt/aichat/app"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Rsync code → root@$DROPLET:$APP_DIR ==="
# Виключаємо venv, cache, logs, .git, локальні artifacts
rsync -avz --progress \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    --exclude '.logs' \
    --exclude '.git' \
    --exclude '.bin' \
    --exclude 'descServ.json' \
    --exclude 'examples/' \
    --exclude 'tests/' \
    --exclude '.env' \
    --exclude '.env.example' \
    "$LOCAL_DIR/" \
    "root@$DROPLET:$APP_DIR/"

echo ""
echo "=== Set ownership ==="
ssh "root@$DROPLET" "chown -R aichat:aichat $APP_DIR"

echo ""
echo "DONE. Next: SSH у droplet, sudo -iu aichat, запусти 02_app_install.sh"
echo "  ssh root@$DROPLET"
echo "  sudo -iu aichat"
echo "  cd /opt/aichat/app/deploy"
echo "  bash 02_app_install.sh"
