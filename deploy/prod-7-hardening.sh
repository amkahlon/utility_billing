#!/usr/bin/env bash
# Production server, step 7 of 7: security hardening (FS-NFR-06).
#   sudo bash prod-7-hardening.sh
# Before running it: log in once with an SSH key (ssh-copy-id), because password logins are switched off here.
set -euo pipefail
. /etc/ums/secrets.env
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ -s /root/.ssh/authorized_keys ] || ls /home/*/.ssh/authorized_keys >/dev/null 2>&1 || {
	echo "No SSH key is set up for any user: add one first, or you'll be locked out."; exit 1; }
apt-get install -yq ufw fail2ban unattended-upgrades

echo "== firewall: SSH, HTTP and HTTPS only"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "== SSH: keys only, no root password login"
cat > /etc/ssh/sshd_config.d/90-ums.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 4
X11Forwarding no
EOF
sshd -t && systemctl reload ssh

echo "== fail2ban: SSH and the site's login"
cat > /etc/fail2ban/filter.d/ums-login.conf <<'EOF'
[Definition]
failregex = ^<HOST> .* "POST /api/method/login HTTP/[0-9.]+" 401
EOF
cat > /etc/fail2ban/jail.d/ums.conf <<'EOF'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
[ums-login]
enabled = true
port = http,https
filter = ums-login
logpath = /var/log/nginx/access.log
maxretry = 10
findtime = 10m
bantime = 1h
EOF
systemctl restart fail2ban

echo "== automatic security updates (reboots at 04:00 when a kernel update needs one)"
cat > /etc/apt/apt.conf.d/52ums-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Mail "root";
EOF
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== databases on this machine only"
grep -q "^bind-address *= *127.0.0.1" /etc/mysql/mariadb.conf.d/90-ums.cnf
sed -i 's/^#\? *bind .*/bind 127.0.0.1 ::1/' /etc/redis/redis.conf && systemctl restart redis-server
mysql -uroot -p"$MARIADB_ROOT_PASSWORD" -e "DELETE FROM mysql.user WHERE User=''; DROP DATABASE IF EXISTS test; FLUSH PRIVILEGES;"

echo "== the site: sessions, passwords, files"
sudo -u frappe -H bash -lc "cd ~/frappe-bench && bench --site $DOMAIN set-config session_expiry 08:00:00"
chmod 600 /home/frappe/frappe-bench/sites/*/site_config.json
chmod 700 /home/frappe/frappe-bench/sites/*/private
echo "Done. Also switch on in the desk (System Settings): password policy (minimum score 3), lock after 5 failed logins, and two-factor"
echo "authentication for the manager roles once each manager has an authenticator app (FS-ACC-09)."
