#!/usr/bin/env bash
# Production server, step 6 of 7: availability checks every 5 minutes, with an e-mail alert when something goes wrong and one when it
# recovers (FS-NFR-04, 13). Checks: the site answers over HTTPS, the background workers and scheduler run, MariaDB and Redis are up,
# disk and memory have room, the last backup is less than 26 hours old, the certificate has more than 14 days left.
#   sudo bash prod-6-monitoring.sh --install   once: the 5-minute check and a daily summary at 07:00
#   sudo bash prod-6-monitoring.sh             one check now (prints the result)
# Add an outside uptime check too (UptimeRobot, Better Stack …) on https://$DOMAIN/api/method/ping: it alerts when the whole server is down.
set -uo pipefail
. "${UMS_SECRETS:-/etc/ums/secrets.env}"
STATE_DIR=${STATE_DIR:-/var/lib/ums}
mkdir -p "$STATE_DIR"

if [ "${1:-}" = "--install" ]; then
	{ echo "*/5 * * * * root bash $(readlink -f "$0") >> /var/log/ums-monitor.log 2>&1"
	  echo "0 7 * * * root bash $(readlink -f "$0") --summary >> /var/log/ums-monitor.log 2>&1"; } > /etc/cron.d/ums-monitor
	cat > /etc/logrotate.d/ums <<'EOF'
/var/log/ums-*.log /var/log/msmtp.log {
	weekly
	rotate 8
	compress
	missingok
	notifempty
}
EOF
	echo "Monitoring installed."
fi

problems=()
check() { "$@" >/dev/null 2>&1 || problems+=("$CHECK"); }
CHECK="the site doesn't answer at https://$DOMAIN"; check curl -fsS --max-time 20 "https://$DOMAIN/api/method/ping"
CHECK="MariaDB is down"; check systemctl is-active --quiet mariadb
CHECK="Redis is down"; check systemctl is-active --quiet redis-server
CHECK="Nginx is down"; check systemctl is-active --quiet nginx
for p in web schedule worker; do
	CHECK="supervisor: no running frappe $p process"; check bash -c "supervisorctl status | grep -E 'frappe.*$p' | grep -q RUNNING"
done
# every PDF (bills, receipts, reports) is drawn by headless Chromium; if it can't start, PDFs fall back to the older engine
CHROME=/home/frappe/frappe-bench/chromium/chrome-linux/headless_shell
CHECK="Chromium for PDFs is missing or doesn't start ($CHROME)"
check sudo -u frappe timeout 30 "$CHROME" --headless --no-sandbox --dump-dom about:blank
disk=$(df --output=pcent / | tail -1 | tr -dc 0-9)
[ "$disk" -lt 85 ] || problems+=("disk ${disk}% full")
mem=$(free | awk '/Mem:/ {printf "%d", ($3/$2)*100}')
[ "$mem" -lt 92 ] || problems+=("memory ${mem}% used")
if [ -f "$STATE_DIR/last-backup" ]; then
	age=$(( ($(date +%s) - $(date -d "$(cut -f1 "$STATE_DIR/last-backup")" +%s)) / 3600 ))
	[ "$age" -lt 26 ] || problems+=("last backup is ${age} hours old")
else
	problems+=("no backup has been taken yet")
fi
if [ -f "/etc/letsencrypt/live/$DOMAIN/cert.pem" ]; then
	end=$(date -d "$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/$DOMAIN/cert.pem" | cut -d= -f2)" +%s)
	days=$(( (end - $(date +%s)) / 86400 ))
	[ "$days" -gt 14 ] || problems+=("HTTPS certificate expires in $days days")
fi

now="$(date '+%F %T')"
if [ ${#problems[@]} -gt 0 ]; then
	msg=$(printf -- '- %s\n' "${problems[@]}")
	echo "$now PROBLEM: ${problems[*]}"
	if [ "$(cat "$STATE_DIR/monitor-state" 2>/dev/null)" != "$msg" ]; then  # alert once per new state, not every 5 minutes
		echo -e "Checks failing on $(hostname) for https://$DOMAIN at $now:\n$msg" | mail -s "UMS ALERT: $DOMAIN" "$ALERT_EMAIL" 2>/dev/null
		echo "$msg" > "$STATE_DIR/monitor-state"
	fi
else
	echo "$now OK (disk ${disk}%, memory ${mem}%)"
	if [ -s "$STATE_DIR/monitor-state" ]; then
		echo "All checks pass again on $(hostname) for https://$DOMAIN at $now." | mail -s "UMS recovered: $DOMAIN" "$ALERT_EMAIL" 2>/dev/null
		: > "$STATE_DIR/monitor-state"
	fi
fi
if [ "${1:-}" = "--summary" ]; then
	{ echo "Daily summary for https://$DOMAIN on $(hostname):"; echo "disk ${disk}%, memory ${mem}%"
	  echo "last backup: $(cat "$STATE_DIR/last-backup" 2>/dev/null || echo none)"
	  echo "open problems: ${problems[*]:-none}"; } | mail -s "UMS daily summary: $DOMAIN" "$ALERT_EMAIL" 2>/dev/null
fi
