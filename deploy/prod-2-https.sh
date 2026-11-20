#!/usr/bin/env bash
# Production server, step 2 of 7: HTTPS from Let's Encrypt, renewed automatically, and security headers on every response.
#   sudo bash prod-2-https.sh      (DNS for $DOMAIN must already point at this server; ports 80 and 443 open)
set -euo pipefail
. /etc/ums/secrets.env
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
apt-get install -yq certbot python3-certbot-nginx
CONF=/home/frappe/frappe-bench/config/nginx.conf
grep -q "server_name $DOMAIN" "$CONF" || { echo "Nginx isn't set up for $DOMAIN yet: run prod-1-provision.sh."; exit 1; }
certbot --nginx --non-interactive --agree-tos --redirect -m "$ALERT_EMAIL" -d "$DOMAIN"
systemctl enable --now certbot.timer   # renews twice a day when due

# security headers (FS-NFR-06): included by the site's server block
cat > /etc/nginx/conf.d/ums-security-headers.inc <<'EOF'
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(self)" always;
EOF
if ! grep -q ums-security-headers "$CONF"; then
	sed -i "0,/server_name $DOMAIN;/s//server_name $DOMAIN;\n\tinclude \/etc\/nginx\/conf.d\/ums-security-headers.inc;/" "$CONF"
fi
sed -i 's/^\(\s*\)# *server_tokens off;/\1server_tokens off;/' /etc/nginx/nginx.conf
nginx -t && systemctl reload nginx
sudo -u frappe -H bash -lc "cd ~/frappe-bench && bench --site $DOMAIN set-config host_name https://$DOMAIN"
echo "Done: https://$DOMAIN. Check it at https://www.ssllabs.com/ssltest/ (aim for A). Next: prod-3-email.sh."
