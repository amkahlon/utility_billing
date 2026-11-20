# Production server build (increment 23)

User decision 2026-11-16: **scripts only for now**. They are run by the user (or an administrator) on the server chosen for the client,
in this order, as root on a fresh **Ubuntu 24.04** server (4 vCPU, 8 GB RAM, 80 GB SSD is a good start for one society of ~20,000 accounts).

| Step | Script | What it does |
|---|---|---|
| 0 | `prod-secrets.env.example` | Copy to `/etc/ums/secrets.env`, fill in, `chmod 600`. Keep the filled-in copy in the client's password manager, never here. |
| 1 | `prod-1-provision.sh` | Packages, Node 24, MariaDB (local only, utf8mb4), Python 3.14 and the bench tool through `uv`, Frappe 16 bench as user `frappe`, the app from GitHub (deploy key), the site, headless Chromium for PDFs (`bench setup-chrome`), supervisor and Nginx. |
| 2 | `prod-2-https.sh` | Let's Encrypt certificate with automatic renewal, HTTP redirected to HTTPS, security headers. Needs DNS pointing at the server. |
| 3 | `prod-3-email.sh` | The site's outgoing Email Account and the server's own alert mail (msmtp), with a test message. |
| 4 | `prod-4-backup.sh --install` | Nightly 01:30 backup: database, files and site config in one file encrypted with AES-256, copied to S3-compatible storage; 30 daily and 12 monthly copies kept. |
| 5 | `prod-5-restore-test.sh --install` | Every Sunday: the newest backup restored into a throwaway site and checked; result mailed. Also the disaster-recovery steps. |
| 6 | `prod-6-monitoring.sh --install` | Every 5 minutes: site, workers, scheduler, MariaDB, Redis, Chromium for PDFs, disk, memory, backup age, certificate; one mail when a problem starts and one when it clears; a daily summary. |
| 7 | `prod-7-hardening.sh` | Firewall (SSH, HTTP, HTTPS only), SSH keys only, fail2ban on SSH and the site login, automatic security updates, databases on localhost, site sessions of 8 hours. |

After step 7, in the desk: System Settings → password policy, lock after failed logins, and two-factor for managers (FS-ACC-09).
Add an outside uptime check on `https://<domain>/api/method/ping`.

## Tested on the laptop (2026-11-16)
See the increment 23 QA report: the backup and restore scripts were run against the QA site with a local folder in place of S3.
Not tested on the laptop (no server, and the tools aren't installed in WSL): the S3 copy with rclone, certbot, Nginx headers, ufw,
fail2ban, msmtp. Check those on the first real server, following the script's own messages.

## Corrected on 2026-11-19, not yet run on a server
`prod-1-provision.sh` installed Node 20 and used Ubuntu's Python 3.12; Frappe 16 needs Node 24 or newer and Python 3.14, so it would
have stopped at `bench init`. It now installs Node 24, and Python 3.14 with the bench tool through `uv` (as the laptop's
`wsl-3-bench-erpnext.sh` does). The first run on a real server is the first test of these steps: watch the "user frappe and bench"
and "Chromium for PDFs" parts.

## PDFs
Every PDF the app makes (bills, receipts, statements, notices, invoices) is drawn by headless Chromium, which step 1 downloads into
the bench. wkhtmltopdf with patched Qt is installed too, as the fallback when Chromium can't start. To force the old engine on a
site: `bench --site <domain> set-config ub_pdf_engine wkhtmltopdf` (remove the key to go back to Chromium).

## Disaster recovery (a lost server)
1. Build a new server with steps 0–3 (same domain and secrets).
2. Take the newest backup from the bucket (`rclone copy ums:<bucket>/daily/<file> .`), decrypt with `BACKUP_PASSPHRASE`
   (`gpg -d file.tar.gpg | tar -xf -`).
3. Put the backup's `encryption_key` in the new site's `site_config.json`, then
   `bench --site <domain> restore <database.sql.gz> --with-public-files <files.tar> --with-private-files <private-files.tar>` and `bench migrate`.
4. Steps 4–7 again.
