#!/usr/bin/env bash
# Cron for booking JSON sync.
# Runs on droplet as root AFTER 10_booking_install.sh.
#
# What it schedules:
#   * categories/services/employees: щогодини (тягне з Postgres booking.salons + ua/pl/gb.*)
#   * freetime: кожні 15 хв (тягне напряму з AIHelps CRM)

set -euo pipefail

CRON_FILE=/etc/cron.d/pdpbooking
APP_DIR=/opt/pdpbooking/app
VENV=/opt/pdpbooking/venv
LOG=/var/log/pdpbooking/cron.log

cat > "$CRON_FILE" <<EOF
# PiedDePoule Booking sync. Managed by deploy/12_booking_cron.sh.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Категорії / послуги / працівники: щогодини в :05
5 * * * * aichat cd $APP_DIR && $VENV/bin/python categories.py >> $LOG 2>&1
6 * * * * aichat cd $APP_DIR && $VENV/bin/python services.py   >> $LOG 2>&1
7 * * * * aichat cd $APP_DIR && $VENV/bin/python employees.py  >> $LOG 2>&1

# Freetime: кожні 15 хв (CRM свіжий час)
*/15 * * * * aichat cd $APP_DIR && $VENV/bin/python freetime.py >> $LOG 2>&1
EOF

chmod 0644 "$CRON_FILE"
touch "$LOG"
chown aichat:aichat "$LOG"
systemctl restart cron || systemctl restart crond || true

echo "DONE. Crontab installed at $CRON_FILE"
echo "Logs: $LOG"
