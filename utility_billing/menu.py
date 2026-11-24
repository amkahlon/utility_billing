# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Each role's sidebar shows the screens it works in (user, 2026-11-21: "trim each role's sidebar to its own screens"; increment 44).

Frappe already leaves out of the sidebar the pages and reports a role may not open (their own role lists) and the DocTypes it may
not read. But many roles may read records they don't work on (a cashier reads bills and runs to find an account), so their
sidebars listed every list in the app. Here each section's DocType lists are shown only to the roles that work in that section.
Nothing else changes: what a role may open and do stays with its permissions, so a link from another screen still works.
System Managers see everything.
"""

import frappe

SIDEBAR = "Utility Management System"

BM, GM, MG, AU = "Billing Manager", "General Manager", "Management", "Billing Auditor"
BO, AO, RO, SA, CA, MR = "Billing Officer", "Accounts Officer", "Recovery Officer", "Society Admin", "Cashier", "Meter Reader"
MANAGERS = (BM, GM)

# section (as in dev/build_reports.py GROUPS) -> the roles whose sidebar lists its DocTypes
SECTION_ROLES: dict[str, tuple[str, ...]] = {
	"Approvals": (*MANAGERS, MG, AU, SA),
	"Users and Access": (GM, SA),
	"Consumers": (*MANAGERS, BO, AU, RO, SA, AO),
	"Meter Readings": (*MANAGERS, BO, AU, MR),
	"Billing": (*MANAGERS, BO, AU),
	"Payments": (*MANAGERS, AO, AU),
	"Messages": (*MANAGERS, BO, SA),
	"Service Requests": (*MANAGERS, BO, SA),
	"Corrections": (*MANAGERS, BO, AU, AO),
	"Recovery": (*MANAGERS, RO, AO, AU, MG),
	"Revenue Assurance": (*MANAGERS, AU, MG),
	"Period Control": (*MANAGERS, AO, AU),
	"Migration": (*MANAGERS, SA),
	"Accounting": (*MANAGERS, AO, AU),
	"Meters and Network": (*MANAGERS, BO, SA),
	"Integrations": (GM, SA),
	"AI Analytics": (*MANAGERS, MG, SA),
	"Licensing": (GM, SA),
	"Setup": (*MANAGERS, SA),
}
# a DocType some other roles also work in, beyond its section's
EXTRA_ROLES: dict[str, tuple[str, ...]] = {
	"Cashier Day": (CA,), "Payment Receipt": (CA,), "Fee Charge": (CA,),  # the cash counter's own records
	"Connection": (CA, MR),  # finding an account
	"Installment Plan": (RO,), "Bill Dispute": (RO,), "Court Hold": (RO,),  # recovery arranges and holds
	"Security Deposit": (CA,),  # taken at the counter
}
# pages whose own role list lets in roles that only pass through them
PAGE_ONLY: dict[str, str] = {"ub-dashboard": "dashboard.view"}  # cashiers and meter readers are sent on to their own screen


def shown(link_type: str, link_to: str, section: str | None, roles: set[str]) -> bool:
	"""Whether a sidebar link is listed for a user with these roles (Frappe's own permission filter has run already)."""
	if "System Manager" in roles:
		return True
	if link_type == "Page" and link_to in PAGE_ONLY:
		from utility_billing.utility_billing import rights  # noqa: PLC0415

		return rights.can(PAGE_ONLY[link_to], roles=roles)
	if link_type != "DocType":
		return True  # pages and reports follow their own role lists
	allowed = set(SECTION_ROLES.get(section or "", ())) | set(EXTRA_ROLES.get(link_to, ()))
	return bool(allowed & roles)


def trim(items: list[dict], roles: set[str]) -> list[dict]:
	"""The sidebar's items without the links not listed for these roles, and without sections left empty."""
	if "System Manager" in roles:
		return items
	out, section, pending = [], None, None
	for item in items:
		if item.get("type") == "Section Break":
			section, pending = item.get("label"), item
			continue
		if item.get("type") == "Link" and not shown(item.get("link_type"), item.get("link_to"), section if item.get("child") else None, roles):
			continue
		if pending is not None and item.get("child"):
			out.append(pending)
			pending = None
		out.append(item)
	return out


def apply(bootinfo, roles=None):
	"""Trim the app's sidebar in the boot information (boot.boot_session)."""
	roles = set(roles if roles is not None else frappe.get_roles())
	entry = (bootinfo.get("module_sidebars") or {}).get(SIDEBAR)
	if entry and entry.get("items"):
		entry["items"] = trim(entry["items"], roles)
