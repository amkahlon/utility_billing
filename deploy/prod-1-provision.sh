#!/usr/bin/env bash
# Production server, step 1 of 7: Ubuntu 24.04 with Frappe 16 and the Utility Management System app, run by supervisor behind Nginx.
# Increment 23 (user decision 2026-11-16: scripts only for now; run them on the chosen server).
#   sudo bash prod-1-provision.sh        (as root, on a fresh Ubuntu 24.04 server with /etc/ums/secrets.env filled in)
# Safe to run again: each step checks whether it is already done.
set -euo pipefail
SECRETS=/etc/ums/secrets.env
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ -f "$SECRETS" ] || { echo "Fill in $SECRETS first (copy of prod-secrets.env.example)."; exit 1; }
[ "$(stat -c %a "$SECRETS")" = 600 ] || { echo "$SECRETS must be chmod 600."; exit 1; }
# shellcheck disable=SC1090
. "$SECRETS"
FUSER=frappe
BENCH=/home/$FUSER/frappe-bench

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# pkg-config and libmariadb-dev: Frappe's database driver (mysqlclient) is compiled during bench init and stops without them
# (third rehearsal run, 2026-11-19); libcups2 and the x fonts: as on the CI machine, for Chromium and wkhtmltopdf
apt-get install -yq git curl ca-certificates build-essential pkg-config python3-dev python3-venv python3-pip pipx \
	mariadb-server mariadb-client libmariadb-dev redis-server libcups2 xfonts-75dpi xfonts-base \
	nginx supervisor xvfb libfontconfig1 fontconfig fonts-noto-core cron gnupg logrotate unattended-upgrades jq \
	libnss3 libnspr4 libasound2t64  # headless Chromium makes every PDF (bills, receipts, reports) and can't start without them
# wkhtmltopdf with patched Qt: only the fallback if Chromium can't start (the Chromium move, 2026-11-18)
if ! wkhtmltopdf --version 2>/dev/null | grep -q "patched qt"; then
	curl -fsSL -o /tmp/wkhtml.deb https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_amd64.deb
	apt-get install -yq /tmp/wkhtml.deb && rm -f /tmp/wkhtml.deb
fi
# Node 24 (Frappe 16 needs 24 or newer; this script said 20 until 2026-11-18, which would have failed) and yarn for the desk assets
if ! node --version 2>/dev/null | grep -q '^v24'; then
	curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
	apt-get install -yq nodejs
fi
command -v yarn >/dev/null || npm install -g yarn

echo "== MariaDB (utf8mb4, local only)"
cat > /etc/mysql/mariadb.conf.d/90-ums.cnf <<'EOF'
[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
bind-address = 127.0.0.1
innodb_buffer_pool_size = 1G
innodb_lock_wait_timeout = 120
[mysql]
default-character-set = utf8mb4
EOF
systemctl restart mariadb
if mysql -uroot -e "select 1" >/dev/null 2>&1; then  # first run: set the root password
	mysql -uroot -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MARIADB_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"
fi

echo "== user $FUSER and bench"
id "$FUSER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$FUSER"
chmod 755 /home/$FUSER
# Frappe 16 needs Python 3.14; Ubuntu 24.04 ships 3.12. uv installs 3.14 and the bench CLI for the frappe user without touching
# the system Python (as on the development laptop, server/wsl-3-bench-erpnext.sh). Until 2026-11-18 this script used the system
# Python through pipx, which would have failed at bench init.
# a clean environment for the frappe user: whatever the admin's shell carries (XDG_CONFIG_HOME, a Python path …) must not leak in
# (found by the rehearsal, 2026-11-19: uv tried to write its settings into the calling user's home)
# and start from the frappe user's own home: uv looks for a uv.toml in the folder it is started from and stops if it can't read
# that folder (second rehearsal run, 2026-11-19: the admin's folder isn't readable by the frappe user)
cd /home/$FUSER
B="sudo -u $FUSER -H env -i HOME=/home/$FUSER USER=$FUSER LOGNAME=$FUSER LANG=C.UTF-8 PATH=/home/$FUSER/.local/bin:/usr/local/bin:/usr/bin:/bin"
# the frappe user must see Node 24: another Node in /usr/local/bin (installed by hand, or by nvm for all users) would come first and
# bench init stops at yarn install (fourth rehearsal run, 2026-11-20)
if ! $B node --version 2>/dev/null | grep -q '^v24'; then
	echo "The frappe user's node is $($B bash -c 'command -v node; node --version' 2>/dev/null | tr '\n' ' '), not Node 24 from /usr/bin."
	echo "Remove the other Node (for example /usr/local/bin/node) and run this script again."
	exit 1
fi
$B bash -c "command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh"
$B bash -c 'uv python install 3.14 && (uv tool install --python "$(uv python find 3.14)" frappe-bench || uv tool upgrade frappe-bench)'
# a bench init that stopped halfway leaves the folder behind without a working Frappe; set it aside so a second run starts afresh
# instead of skipping the step as done
if [ -d "$BENCH" ] && ! $B bash -c "$BENCH/env/bin/python -c 'import frappe'" >/dev/null 2>&1; then
	mv "$BENCH" "$BENCH.failed-$(date +%Y%m%d-%H%M%S)"
	echo "   An unfinished bench was set aside; it is built again."
fi
if [ ! -d "$BENCH" ]; then
	$B bash -c "cd /home/$FUSER && bench init --frappe-branch $FRAPPE_BRANCH --python \"\$(uv python find 3.14)\" --skip-redis-config-generation frappe-bench"
fi
if [ ! -d "$BENCH/apps/utility_billing" ]; then
	echo "   The app comes from $APP_REPO: add /home/$FUSER/.ssh/id_ed25519.pub as a read-only deploy key on GitHub first."
	[ -f /home/$FUSER/.ssh/id_ed25519 ] || $B ssh-keygen -t ed25519 -N "" -f /home/$FUSER/.ssh/id_ed25519 -C "ums-deploy@$DOMAIN"
	$B bash -c "cd $BENCH && GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new' bench get-app --branch $APP_BRANCH $APP_REPO"
fi

echo "== site $DOMAIN"
if [ ! -d "$BENCH/sites/$DOMAIN" ]; then
	$B bash -c "cd $BENCH && bench new-site $DOMAIN --mariadb-root-password '$MARIADB_ROOT_PASSWORD' --admin-password '$SITE_ADMIN_PASSWORD' --install-app utility_billing"
fi
$B bash -c "cd $BENCH && bench use $DOMAIN && bench --site $DOMAIN set-config maintenance_mode 0 && bench --site $DOMAIN enable-scheduler"
# keep the site's encryption key safe: without it, saved passwords and API secrets can't be read after a restore
echo "   Copy encryption_key from $BENCH/sites/$DOMAIN/site_config.json into the client's password manager now."

echo "== Chromium for PDFs"
# every PDF the app makes is drawn by headless Chromium; download it now, not on the first bill a user prints
$B bash -c "cd $BENCH && bench setup-chrome"
$B bash -c "cd $BENCH && bench --site $DOMAIN execute utility_billing.pdf.chrome_works" | grep -q True \
	|| echo "   WARNING: Chromium doesn't start on this server; PDFs fall back to wkhtmltopdf. Check the libraries above."

echo "== production processes (supervisor + Nginx)"
$B bash -c "cd $BENCH && bench build --app utility_billing"
cd "$BENCH" && /home/$FUSER/.local/bin/bench setup production "$FUSER" --yes
supervisorctl reread && supervisorctl update
systemctl reload nginx
echo "Done. Next: prod-2-https.sh (needs DNS for $DOMAIN pointing here)."
