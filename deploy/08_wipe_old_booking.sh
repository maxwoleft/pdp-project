#!/usr/bin/env bash
# Wipe ANY existing booking deployment on the droplet before clean install.
# Runs on the DROPLET as root.
#
# What it does:
#   * Stops + disables any old booking-related systemd unit (auto-detect).
#   * Removes nginx vhosts/symlinks pointing at booking.p-de-p.com.
#   * Backs up old application directory to /root/booking-backup-<timestamp>.tgz.
#   * Removes /opt/pdpbooking, /opt/booking, /var/www/booking* (best effort).
#   * Frees port 5050 if held.
#
# Safe to re-run. NEVER touches AI Chat (ai-chat.p-de-p.com / aichat.service).

set -euo pipefail

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/booking-backup-$TS.tgz"

echo "=== [1/6] Stop any old booking systemd units ==="
for unit in pdpbooking booking booking-london booking_london_predict; do
    if systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null | grep -q "${unit}.service"; then
        echo "  stopping ${unit}.service"
        systemctl stop "${unit}.service" || true
        systemctl disable "${unit}.service" || true
        rm -f "/etc/systemd/system/${unit}.service"
    fi
done
systemctl daemon-reload

echo "=== [2/6] Remove nginx vhosts for booking.p-de-p.com ==="
for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
    [ -f "$f" ] || continue
    if grep -q "booking.p-de-p.com" "$f" 2>/dev/null; then
        echo "  removing $f"
        rm -f "$f"
    fi
done
# Reload only if config still valid (so we don't kill the box)
nginx -t && systemctl reload nginx || echo "  ! nginx config left in inconsistent state, fix before continuing"

echo "=== [3/6] Backup old application directories ==="
TARGETS=()
for d in /opt/pdpbooking /opt/booking /opt/booking_london_predict /var/www/booking /var/www/booking.p-de-p.com /home/*/booking_london_predict; do
    [ -d "$d" ] && TARGETS+=("$d")
done
if [ ${#TARGETS[@]} -gt 0 ]; then
    echo "  archiving: ${TARGETS[*]}"
    tar -czf "$BACKUP" --warning=no-file-changed "${TARGETS[@]}" 2>/dev/null || true
    echo "  -> $BACKUP ($(du -h "$BACKUP" | cut -f1))"
else
    echo "  nothing to back up"
fi

echo "=== [4/6] Remove old application directories ==="
for t in "${TARGETS[@]:-}"; do
    echo "  rm -rf $t"
    rm -rf "$t"
done

echo "=== [5/6] Kill any process bound to booking port (5050) ==="
if ss -ltnp 2>/dev/null | grep -q ':5050 '; then
    PID="$(ss -ltnp 2>/dev/null | awk '/:5050 / {match($0,/pid=([0-9]+)/,a); print a[1]; exit}')"
    if [ -n "${PID:-}" ]; then
        echo "  killing PID $PID"
        kill "$PID" || true
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
    fi
fi

echo "=== [6/6] Clean Let's Encrypt cert (will be re-issued on fresh deploy) ==="
if [ -d /etc/letsencrypt/live/booking.p-de-p.com ]; then
    echo "  certbot delete --cert-name booking.p-de-p.com"
    certbot delete --non-interactive --cert-name booking.p-de-p.com || true
fi

echo
echo "DONE. Old booking wiped. Backup at: $BACKUP"
echo "Next: bash 09_booking_deploy.sh  (on local Mac)"
echo "Then: bash 10_booking_install.sh  (on droplet as root)"
