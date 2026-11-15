"""Quality gate: none of our DocTypes may share a name with a DocType of another installed app (ERPNext, HRMS, ...).
In increment 18 our "Asset Maintenance" took over ERPNext's; this catches that before tests run.
Run from ~/frappe-bench/sites:  ../env/bin/python ../apps/utility_billing/e2e/check_clash.py test.localhost
Exit code 1 when a clash is found, or when a DocType of ours is registered under another app's module."""
import json
import sys
from pathlib import Path

import frappe

site = sys.argv[1] if len(sys.argv) > 1 else "test.localhost"
frappe.init(site=site, sites_path=".")
frappe.connect()
ours = Path(frappe.get_app_path("utility_billing", "utility_billing", "doctype"))
names = [json.loads((p / f"{p.name}.json").read_text())["name"] for p in ours.iterdir() if (p / f"{p.name}.json").exists()]
clashes = []
for n in names:
	for app in frappe.get_installed_apps():
		if app == "utility_billing":
			continue
		for mod in frappe.get_module_list(app):
			if Path(frappe.get_app_path(app, frappe.scrub(mod), "doctype", frappe.scrub(n), frappe.scrub(n) + ".json")).exists():
				clashes.append(f"{n}: also in {app} ({mod})")
	module = frappe.db.get_value("DocType", n, "module")
	if module and module != "Utility Billing":
		clashes.append(f"{n}: registered under module {module} in the database")
for c in clashes:
	print("CLASH", site, c)
print(f"clash check on {site}: {len(names)} DocTypes, {len(clashes)} clashes ({', '.join(frappe.get_installed_apps())})")
sys.exit(1 if clashes else 0)
