#!/usr/bin/env bash
# Production server, step 4 of 7: nightly encrypted backup of the database and files, copied off the server to S3-compatible storage
# (user decision 2026-11-16), keeping KEEP_DAILY daily and KEEP_MONTHLY monthly copies (FS-NFR-05).
#   sudo bash prod-4-backup.sh --install   once: installs rclone and the 01:30 nightly job
#   sudo bash prod-4-backup.sh             a backup now (what the nightly job runs)
# Each backup is one file, ums-<site>-<YYYYmmdd-HHMM>.tar.gpg: the database, public and private files and site_config.json (which holds
# the encryption key), encrypted with BACKUP_PASSPHRASE (AES-256). prod-5-restore-test.sh proves a backup can be restored.
# For a test on a laptop, set BACKUP_DEST to a local folder instead of S3, and UMS_SECRETS / BENCH / SITE / STATE_DIR as needed.
set -euo pipefail
SECRETS=${UMS_SECRETS:-/etc/ums/secrets.env}
# shellcheck disable=SC1090
. "$SECRETS"
BENCH=${BENCH:-/home/frappe/frappe-bench}
SITE=${SITE:-$DOMAIN}
STATE_DIR=${STATE_DIR:-/var/lib/ums}
DEST=${BACKUP_DEST:-ums:$S3_BUCKET}            # "ums:<bucket>" is the rclone remote below; a path starting with / is a local folder
RUN_AS=${RUN_AS:-frappe}
STAMP=$(date +%Y%m%d-%H%M)
NAME="ums-$SITE-$STAMP.tar.gpg"
BENCH_BIN=${BENCH_BIN:-/home/$RUN_AS/.local/bin/bench}   # bench is installed for the bench user (uv tool, prod-1)

export RCLONE_CONFIG_UMS_TYPE=s3 RCLONE_CONFIG_UMS_PROVIDER="${S3_PROVIDER:-AWS}" RCLONE_CONFIG_UMS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID:-}" \
	RCLONE_CONFIG_UMS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY:-}" RCLONE_CONFIG_UMS_REGION="${S3_REGION:-}" \
	RCLONE_CONFIG_UMS_ENDPOINT="${S3_ENDPOINT:-}" RCLONE_CONFIG_UMS_SERVER_SIDE_ENCRYPTION="" RCLONE_CONFIG_UMS_NO_CHECK_BUCKET=true

alert() {
	echo "BACKUP FAILED: $1" >&2
	command -v mail >/dev/null && echo "Backup of $SITE on $(hostname) failed at $(date): $1" | mail -s "UMS backup FAILED: $SITE" "${ALERT_EMAIL:-root}" || true
}
trap 'alert "line $LINENO"' ERR

if [ "${1:-}" = "--install" ]; then
	[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
	command -v rclone >/dev/null || apt-get install -yq rclone
	mkdir -p "$STATE_DIR"
	echo "30 1 * * * root bash $(readlink -f "$0") >> /var/log/ums-backup.log 2>&1" > /etc/cron.d/ums-backup
	echo "Nightly backup at 01:30 installed. Running one now…"
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"
chown "$RUN_AS" "$WORK" 2>/dev/null || true
as_bench() { if [ "$(id -un)" = "$RUN_AS" ]; then bash -c "$1"; else sudo -u "$RUN_AS" -H bash -c "$1"; fi; }

echo "== $(date) backup of $SITE"
as_bench "cd '$BENCH' && $BENCH_BIN --site '$SITE' backup --with-files --compress --backup-path '$WORK' >/dev/null"
cp "$BENCH/sites/$SITE/site_config.json" "$WORK/site_config.json"
ls "$WORK"/*database.sql.gz >/dev/null   # the database dump must be there
mkdir -p "$WORK/out"
tar -C "$WORK" -cf - --exclude ./out . | gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
	--passphrase-fd 3 -o "$WORK/out/$NAME" 3<<<"$BACKUP_PASSPHRASE"
SIZE=$(stat -c %s "$WORK/out/$NAME")

copy() {  # copy <file> <folder under the destination>
	if [[ "$DEST" == /* ]]; then mkdir -p "$DEST/$2" && cp "$1" "$DEST/$2/"; else rclone copy --s3-no-check-bucket "$1" "$DEST/$2/"; fi
}
prune() {  # prune <folder> <age>, e.g. daily 30d
	if [[ "$DEST" == /* ]]; then
		[ -d "$DEST/$1" ] && find "$DEST/$1" -name 'ums-*.tar.gpg' -mtime "+${2%d}" -delete
	else
		rclone delete --min-age "$2" "$DEST/$1" 2>/dev/null || true   # an empty folder (no monthly copy yet) isn't an error
	fi
	return 0
}
copy "$WORK/out/$NAME" daily
[ "$(date +%d)" = 01 ] && copy "$WORK/out/$NAME" monthly
prune daily "${KEEP_DAILY:-30}d"
prune monthly "$(( ${KEEP_MONTHLY:-12} * 31 ))d"

mkdir -p "$STATE_DIR"
printf '%s\t%s\t%s\n' "$(date -Iseconds)" "$NAME" "$SIZE" > "$STATE_DIR/last-backup"
echo "== done: $NAME ($((SIZE / 1024)) KB) to $DEST/daily"
