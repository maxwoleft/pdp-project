#!/usr/bin/env bash
# Запускається ВІД ІМЕНІ aichat user (sudo -iu aichat).
# Створює venv, ставить dependencies, готує .env.

set -euo pipefail

APP_HOME="/opt/aichat"
APP_DIR="$APP_HOME/app"
VENV="$APP_HOME/venv"

cd "$APP_DIR"

echo "=== [1/4] Python venv ==="
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

pip install --upgrade pip wheel

echo "=== [2/4] Install dependencies ==="
pip install -e .

echo "=== [3/4] Init DB schema ==="
# Створює всі таблиці (metadata.create_all) + applied миграції
python -m scripts.init_db

# Міграції
python -m scripts.migrate_service_attributes
python -m scripts.migrate_category_groups

echo "=== [4/4] Embed_services + seed admin ==="
echo "Раджу далі вручну на droplet:"
echo "  python -m scripts.seed_admin --email admin@local --password STRONG_PW"
echo "  python -m scripts.sync_from_crm --all"
echo "  python -m scripts.embed_services"
echo "  python -m scripts.embed_options"
