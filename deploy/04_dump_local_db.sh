#!/usr/bin/env bash
# Дамп локальної БД (aichat-pg на Mac) + завантаження в droplet.
# Запускається на ЛОКАЛЬНОМУ Mac.

set -euo pipefail

DROPLET="${DROPLET:-167.172.100.158}"
DUMP_FILE="/tmp/aichat_dump.sql.gz"

echo "=== [1/3] Dump local DB ==="
docker exec aichat-pg pg_dump -U aichat -d aichat --clean --if-exists \
    | gzip > "$DUMP_FILE"
echo "Dump size: $(du -h $DUMP_FILE | cut -f1)"

echo "=== [2/3] Upload to droplet ==="
scp "$DUMP_FILE" "root@$DROPLET:/tmp/aichat_dump.sql.gz"

echo "=== [3/3] Restore on droplet ==="
ssh "root@$DROPLET" <<'REMOTE'
set -euo pipefail
echo "Stopping any DB connections..."
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='aichat' AND pid <> pg_backend_pid();" || true
echo "Restoring..."
gunzip -c /tmp/aichat_dump.sql.gz | sudo -u postgres psql -d aichat
echo "Vacuum + analyze..."
sudo -u postgres psql -d aichat -c "VACUUM ANALYZE;"
echo "DB restored."
rm -f /tmp/aichat_dump.sql.gz
REMOTE

echo "DONE."
