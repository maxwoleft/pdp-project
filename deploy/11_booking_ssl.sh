#!/usr/bin/env bash
# Issue Let's Encrypt SSL cert for booking.p-de-p.com.
# Runs on droplet as root AFTER 10_booking_install.sh.
#
# Requires DNS A record booking.p-de-p.com → droplet IP already in place.
# certbot will modify /etc/nginx/sites-available/pdpbooking — that's fine,
# our nginx_booking.conf is already deployed with HTTP listen.

set -euo pipefail

DOMAIN="booking.p-de-p.com"
EMAIL="${LETSENCRYPT_EMAIL:-max.woleft@gmail.com}"

echo "=== [1/3] Verify DNS resolution ==="
RESOLVED="$(dig +short "$DOMAIN" | tail -n1)"
SERVER_IP="$(curl -s4 https://ifconfig.io)"
if [ "$RESOLVED" != "$SERVER_IP" ]; then
    echo "WARNING: $DOMAIN resolves to '$RESOLVED' but server IP is '$SERVER_IP'"
    echo "         If you just changed DNS, wait for propagation (TTL)."
    read -p "Continue anyway? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
fi

echo "=== [2/3] Issue cert via certbot --nginx ==="
certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --domains "$DOMAIN" \
    --redirect

echo "=== [3/3] Reload nginx + verify HTTPS ==="
nginx -t && systemctl reload nginx
sleep 2
curl -sI "https://$DOMAIN/" | head -5 || echo "  ! HTTPS not responding"

echo
echo "DONE. https://$DOMAIN is live."
echo "Certbot will auto-renew via systemd timer (already enabled by AI Chat install)."
