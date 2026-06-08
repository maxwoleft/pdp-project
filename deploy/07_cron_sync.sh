#!/usr/bin/env bash
# Cron job для daily sync_from_crm + embed_services + embed_options.
# Запускається на DROPLET як root.

set -euo pipefail

# Cron file
cat > /etc/cron.d/aichat-sync <<'CRON'
# AI Chat — щоденний sync з CRM (3:00 ранку)
SHELL=/bin/bash
PATH=/opt/aichat/venv/bin:/usr/bin:/bin
MAILTO=""

# Daily sync о 3:00
0 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.sync_from_crm --all >> /var/log/aichat/sync.log 2>&1

# Auto-link missing keys to existing profiles о 3:25 (після sync)
25 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country ua --apply >> /var/log/aichat/auto_link.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country pl --apply >> /var/log/aichat/auto_link.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country gb --apply >> /var/log/aichat/auto_link.log 2>&1

# LLM-генерація expert key_descriptions о 3:28 (для нових авто-link keys)
28 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.llm_fill_key_descriptions --country ua --apply >> /var/log/aichat/llm_keys.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.llm_fill_key_descriptions --country pl --apply >> /var/log/aichat/llm_keys.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.llm_fill_key_descriptions --country gb --apply >> /var/log/aichat/llm_keys.log 2>&1

# Embed translations о 3:30 (після LLM key_descriptions update)
30 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.embed_translations >> /var/log/aichat/embed.log 2>&1

# Recompute profile.salon_ids/cities о 3:40 (після sync — services могли змінитись)
40 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.compute_profile_salons --apply >> /var/log/aichat/salons.log 2>&1

# Cleanup add-on (idempotent — на випадок якщо CRM додає нові add-on)
15 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.cleanup_addon_categories --apply >> /var/log/aichat/cleanup.log 2>&1
# Cleanup addon services (по NAME pattern: додатково/дополнительн/add-on)
18 3 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.cleanup_addon_services --apply >> /var/log/aichat/cleanup.log 2>&1

# ─── Midday sync (light) — без LLM/embed (OpenAI cost), тільки оновити catalog ───
# 13:00 — повний sync з CRM
0 13 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.sync_from_crm --all >> /var/log/aichat/sync.log 2>&1
# 13:15 — cleanup add-on
15 13 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.cleanup_addon_categories --apply >> /var/log/aichat/cleanup.log 2>&1
# 13:18 — cleanup addon services
18 13 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.cleanup_addon_services --apply >> /var/log/aichat/cleanup.log 2>&1
# 13:25 — link нові services до existing profiles
25 13 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country ua --apply >> /var/log/aichat/auto_link.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country pl --apply >> /var/log/aichat/auto_link.log 2>&1 ; /opt/aichat/venv/bin/python -m scripts.auto_link_missing_keys --country gb --apply >> /var/log/aichat/auto_link.log 2>&1
# 13:40 — recompute profile.salon_ids
40 13 * * * aichat cd /opt/aichat/app && /opt/aichat/venv/bin/python -m scripts.compute_profile_salons --apply >> /var/log/aichat/salons.log 2>&1
CRON

chmod 644 /etc/cron.d/aichat-sync
systemctl reload cron 2>/dev/null || service cron reload

echo "Cron installed. View: cat /etc/cron.d/aichat-sync"
