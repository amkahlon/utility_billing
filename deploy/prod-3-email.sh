#!/usr/bin/env bash
# Production server, step 3 of 7: outgoing e-mail for the site (notices, password resets) and for the server's own alerts.
#   sudo bash prod-3-email.sh
set -euo pipefail
. /etc/ums/secrets.env
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }

echo "== the site's outgoing Email Account"
cat > /tmp/ums_email.py <<'EOF'
import os, frappe
frappe.init(site=os.environ["DOMAIN"], sites_path="."); frappe.connect()
name = "UMS Outgoing"
doc = frappe.get_doc("Email Account", name) if frappe.db.exists("Email Account", name) else frappe.new_doc("Email Account")
doc.update({"email_account_name": name, "email_id": os.environ["SMTP_USER"], "login_id": os.environ["SMTP_USER"],
            "password": os.environ["SMTP_PASSWORD"], "smtp_server": os.environ["SMTP_HOST"], "smtp_port": int(os.environ["SMTP_PORT"]),
            "use_tls": 1, "enable_outgoing": 1, "default_outgoing": 1, "enable_incoming": 0, "always_use_account_email_id_as_sender": 1})
doc.save(ignore_permissions=True); frappe.db.commit(); print("Email Account saved:", doc.name); frappe.destroy()
EOF
chown frappe /tmp/ums_email.py
sudo -u frappe -H env DOMAIN="$DOMAIN" SMTP_USER="$SMTP_USER" SMTP_PASSWORD="$SMTP_PASSWORD" SMTP_HOST="$SMTP_HOST" SMTP_PORT="$SMTP_PORT" \
	bash -c "cd ~/frappe-bench/sites && ../env/bin/python /tmp/ums_email.py"
rm -f /tmp/ums_email.py

echo "== the server's own mail (alerts from prod-4 and prod-6)"
apt-get install -yq msmtp msmtp-mta mailutils
umask 077
cat > /etc/msmtprc <<EOF
defaults
auth on
tls on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
logfile /var/log/msmtp.log
account ums
host $SMTP_HOST
port $SMTP_PORT
from $SMTP_USER
user $SMTP_USER
password $SMTP_PASSWORD
account default : ums
EOF
chmod 600 /etc/msmtprc
echo "E-mail is set up on $(hostname) for https://$DOMAIN." | mail -s "UMS server: e-mail test" "$ALERT_EMAIL"
echo "Done: a test message went to $ALERT_EMAIL. Next: prod-4-backup.sh."
