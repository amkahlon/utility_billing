#!/usr/bin/env bash
# Production server, step 5 of 7: prove the latest backup can be restored (FS-NFR-05). Takes the newest backup from the backup
# destination, decrypts it, restores it into a throwaway site, checks the data is there, then drops the throwaway site.
#   sudo bash prod-5-restore-test.sh --install   once: runs it every Sunday at 03:00 and mails the result
#   sudo bash prod-5-restore-test.sh             a restore test now
# The same steps restore a real site after a disaster: decrypt, then bench --site <site> restore with the files and the old
# site_config.json's encryption_key (see the end of this script).
set -euo pipefail
SECRETS=${UMS_SECRETS:-/etc/ums/secrets.env}
# shellcheck disable=SC1090
. "$SECRETS"
BENCH=${BENCH:-/home/frappe/frappe-bench}
SITE=${SITE:-$DOMAIN}
DEST=${BACKUP_DEST:-ums:$S3_BUCKET}
RUN_AS=${RUN_AS:-frappe}
TEST_SITE=${TEST_SITE:-restore-test.localhost}
BENCH_BIN=${BENCH_BIN:-/home/$RUN_AS/.local/bin/bench}   # bench is installed for the bench user (uv tool, prod-1)
export RCLONE_CONFIG_UMS_TYPE=s3 RCLONE_CONFIG_UMS_PROVIDER="${S3_PROVIDER:-AWS}" RCLONE_CONFIG_UMS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID:-}" \
	RCLONE_CONFIG_UMS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY:-}" RCLONE_CONFIG_UMS_REGION="${S3_REGION:-}" RCLONE_CONFIG_UMS_ENDPOINT="${S3_ENDPOINT:-}"
report() { command -v mail >/dev/null && echo "$2" | mail -s "UMS restore test $1: $SITE" "${ALERT_EMAIL:-root}" || true; }
trap 'report FAILED "The restore test on $(hostname) failed at line $LINENO; see /var/log/ums-restore-test.log."' ERR

if [ "${1:-}" = "--install" ]; then
	echo "0 3 * * 0 root bash $(readlink -f "$0") >> /var/log/ums-restore-test.log 2>&1" > /etc/cron.d/ums-restore-test
	echo "Weekly restore test installed (Sundays 03:00). Running one now…"
fi

WORK=$(mktemp -d)
cleanup() {
	if [ -d "$BENCH/sites/$TEST_SITE" ]; then
		as_bench "cd '$BENCH' && $BENCH_BIN drop-site '$TEST_SITE' --mariadb-root-password '$MARIADB_ROOT_PASSWORD' --force --no-backup >/dev/null" || true
	fi
	rm -rf "$WORK"
}
as_bench() { if [ "$(id -un)" = "$RUN_AS" ]; then bash -c "$1"; else sudo -u "$RUN_AS" -H bash -c "$1"; fi; }
trap cleanup EXIT
chmod 755 "$WORK"

echo "== $(date) restore test of the newest backup of $SITE"
if [[ "$DEST" == /* ]]; then
	LATEST=$(ls -1t "$DEST"/daily/ums-"$SITE"-*.tar.gpg | head -1)
	cp "$LATEST" "$WORK/"
else
	LATEST=$(rclone lsf "$DEST/daily/" --include "ums-$SITE-*.tar.gpg" | sort | tail -1)
	rclone copy "$DEST/daily/$LATEST" "$WORK/"
fi
FILE="$WORK/$(basename "$LATEST")"
echo "   backup: $(basename "$FILE") ($(( $(stat -c %s "$FILE") / 1024 )) KB)"
mkdir "$WORK/x"
gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 -d "$FILE" 3<<<"$BACKUP_PASSPHRASE" | tar -C "$WORK/x" -xf -
chmod -R a+rX "$WORK/x"
DB=$(ls "$WORK"/x/*database.sql.gz)
PUB=$(ls "$WORK"/x/*-files.tar 2>/dev/null || true)
PRIV=$(ls "$WORK"/x/*-private-files.tar 2>/dev/null || true)
KEY=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('encryption_key',''))" "$WORK/x/site_config.json")

cleanup_site_only() { [ -d "$BENCH/sites/$TEST_SITE" ] && as_bench "cd '$BENCH' && $BENCH_BIN drop-site '$TEST_SITE' --mariadb-root-password '$MARIADB_ROOT_PASSWORD' --force --no-backup >/dev/null" || true; }
cleanup_site_only
as_bench "cd '$BENCH' && $BENCH_BIN new-site '$TEST_SITE' --mariadb-root-password '$MARIADB_ROOT_PASSWORD' --admin-password \"\$(openssl rand -hex 16)\" >/dev/null"
as_bench "cd '$BENCH' && $BENCH_BIN --site '$TEST_SITE' set-config encryption_key '$KEY' >/dev/null"
as_bench "cd '$BENCH' && $BENCH_BIN --site '$TEST_SITE' restore '$DB' ${PUB:+--with-public-files '$PUB'} ${PRIV:+--with-private-files '$PRIV'} \
	--mariadb-root-password '$MARIADB_ROOT_PASSWORD' --force >/dev/null"
as_bench "cd '$BENCH' && $BENCH_BIN --site '$TEST_SITE' migrate >/dev/null"

# the restored data: the same counts as the live site had when the backup was taken can't be known here, so check what matters is there
COUNTS=$(as_bench "cd '$BENCH' && echo \"import frappe; print({d: frappe.db.count(d) for d in ('Society', 'Connection', 'Utility Bill', 'Customer Ledger Entry', 'Payment Receipt')})\" \
	| $BENCH_BIN --site '$TEST_SITE' console 2>/dev/null | grep -o \"{.*}\" | tail -1")
echo "   restored: $COUNTS"
echo "$COUNTS" | grep -q "'Society': [1-9]" || { echo "   no society in the restored data"; false; }
report PASSED "The newest backup of $SITE ($(basename "$FILE")) was restored into a throwaway site on $(hostname) and checked: $COUNTS"
echo "== restore test PASSED"
