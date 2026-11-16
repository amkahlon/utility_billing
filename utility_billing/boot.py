# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Adds the browser files' version (see ui_version.py) and the user's landing page to the page's boot information."""

import frappe

from utility_billing.ui_version import ui_version
from utility_billing.utility_billing import rights


def home_page(roles=None) -> str:
	"""Where the app opens for this user: the dashboard for those who may see it, else the screen their role works in. Meter readers
	and cashiers used to land on the dashboard and get "No permission for Page" (bug report 2026-11-16)."""
	roles = set(roles if roles is not None else frappe.get_roles())
	if rights.can("dashboard.view", roles=roles):
		return "ub-dashboard"
	if "Cashier" in roles:
		return "ub-cash"
	if "Meter Reader" in roles:
		return "ub-readings"
	return "ub-consumer-360" if roles & {"Billing Auditor"} else "List/Connection"


PARENT_FIELDS = ("society", "utility", "feeder")


def link_parents() -> dict[str, list[str]]:
	"""{DocType: the parent fields it carries (society, utility, feeder)} for the app's DocTypes: a picker for one of them on any list,
	report or form offers only the records that match the parent already chosen on the screen (user's request 2026-11-16: "when block
	A is selected, only its route should be offered ... application wide for all filters")."""
	key = "ub-link-parents"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return cached
	rows = frappe.db.sql("""select f.parent, f.fieldname, f.options from `tabDocField` f join `tabDocType` d on d.name = f.parent
	                        where d.module = 'Utility Billing' and d.istable = 0 and f.fieldtype = 'Link' and f.fieldname in %(f)s""",
	                     {"f": PARENT_FIELDS}, as_dict=True)
	targets = {"society": "Society", "utility": "Utility Type", "feeder": "Feeder"}
	out = {}
	for r in rows:
		if r.options == targets[r.fieldname]:
			out.setdefault(r.parent, []).append(r.fieldname)
	frappe.cache.set_value(key, out, expires_in_sec=3600)
	return out


def boot_session(bootinfo):
	bootinfo.ub_ui_version = ui_version()
	bootinfo.ub_home = home_page()
	bootinfo.ub_rights = rights.mine()
	bootinfo.ub_link_parents = link_parents()
