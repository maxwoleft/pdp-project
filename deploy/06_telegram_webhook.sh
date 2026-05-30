#!/usr/bin/env bash
# Реєструє Telegram webhook на https://ai-chat.p-de-p.com.
# Запускається на DROPLET від імені aichat.

set -euo pipefail

cd /opt/aichat/app
source /opt/aichat/venv/bin/activate

DOMAIN="ai-chat.p-de-p.com"

# Реєструємо bot для country UA (наприклад). Адаптуй під свою country.
# Формат: --country (ua|pl|gb) --bot-token TOKEN --public-url URL
python -m scripts.register_telegram_bot \
    --country ua \
    --bot-token "$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)" \
    --public-url "https://$DOMAIN" \
    --display-name "AI Chat UA"

echo ""
echo "Webhook зареєстровано. Перевірка:"
echo "  curl https://api.telegram.org/bot\$TOKEN/getWebhookInfo"
