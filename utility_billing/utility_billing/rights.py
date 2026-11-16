# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Action rights (increment 25, user's requirement 2026-11-16: "all the permissions configurable ... let the society owner decide").

Who may do something that is not simply reading or writing one kind of record (open the cash counter, reopen a month, approve a
cashier day, export journals...) is an Action Right record: one per action, with its roles, ticked on the Role Rights screen
(page ub-rights). ACTIONS below lists the actions and the roles they start with; a starting list is written only for an action
that has no record yet, so a society's choices survive updates. Rights on records stay on Frappe's Role Permissions screen, and
who approves a document on its workflow.

Code asks `can("cash.counter")` or `require("cash.counter")`, never for a role by name. A System Manager always passes, so the
site's owner can't lock themselves out; Administrator too."""

import frappe
from frappe import _

# role groups the starting lists are made from (they are only defaults: what counts is the Action Right records)
EXEC = ["Management", "Billing Auditor", "General Manager", "Billing Manager"]
FINANCE = [*EXEC, "Accounts Officer"]
BILLING = [*EXEC, "Billing Officer"]
RECOVERY = [*EXEC, "Recovery Officer", "Accounts Officer"]
READINGS = [*BILLING, "Meter Reader"]

# code: (area, label, what it allows, starting roles)
ACTIONS = {
	# dashboards and analysis
	"dashboard.view": ("Dashboards", "See the management dashboard", "The dashboard's figures, its Insights card and the KPI drill-downs.",
	                   [*FINANCE, *BILLING, *RECOVERY, "Society Admin"]),
	"preview.view": ("Dashboards", "Preview a tariff's effect", "Work out what a draft tariff would bill, before it is approved.",
	                 [*EXEC, "Billing Officer", "Society Admin", "Accounts Officer"]),
	"network.view": ("Dashboards", "See network losses", "Feeder and transformer losses and the loss alerts.",
	                 ["Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Management", "Billing Auditor", "Accounts Officer"]),
	"anomaly.view": ("Dashboards", "See theft suspicion scores", "Accounts scored for unusual use, with the reasons.",
	                 [*RECOVERY, "Billing Officer", "Billing Manager"]),
	# consumers
	"consumer360.view": ("Consumers", "Open Consumer 360", "Search consumers and see everything about an account on one page.",
	                     ["Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Cashier", "Recovery Officer",
	                      "Accounts Officer", "Management", "Billing Auditor"]),
	"servicereq.setup": ("Consumers", "Set up service request types", "Create the society's standard request types and their steps.",
	                     ["Society Admin", "Billing Manager"]),
	# readings
	"readings.view": ("Meter readings", "See the reading dashboard and review queue", "Reading progress, flagged readings and check meters.",
	                  [*READINGS]),
	"readings.review": ("Meter readings", "Approve, correct or send readings for re-read", "Decide flagged readings in the review queue.",
	                    ["Billing Officer", "Billing Manager"]),
	# billing
	"delivery.record": ("Billing", "Record bill delivery", "Mark a run's bills as printed, handed to a distributor or delivered.",
	                    ["Billing Manager", "General Manager", "Billing Officer"]),
	"snapshot.restore": ("Billing", "Restore a run snapshot", "Put back the tariffs and settings a bill run was calculated with.",
	                     ["General Manager", "Billing Manager"]),
	# payments
	"cash.counter": ("Payments", "Use the cash counter", "Open the Cash Counter screen and post counter payments.", ["Cashier", "Accounts Officer"]),
	"cash.any_day": ("Payments", "Work in another cashier's day", "Add receipts to, or close, a cashier day opened by someone else.", []),
	"cash.approve_day": ("Payments", "Approve a cashier day", "Check a closed day's cash count and deposit and approve it.",
	                     ["Billing Manager", "Accounts Officer"]),
	"payments.channel": ("Payments", "Post bank and channel payments", "Payments received through banks, 1Link, Kuickpay and wallets.",
	                     ["Accounts Officer"]),
	"payments.reverse": ("Payments", "Reverse a payment or fee", "Undo a posted payment (for example a dishonoured cheque) or fee.",
	                     ["Billing Manager"]),
	"settlement.post": ("Payments", "Post settlement files", "Import a bank or channel settlement file and post its payments.", ["Accounts Officer"]),
	"settlement.handle": ("Payments", "Match settlement exceptions", "Match unmatched settlement lines and suspense payments.",
	                      ["Accounts Officer", "Billing Manager"]),
	# corrections
	"disputes.decide": ("Corrections", "Decide a bill dispute", "Accept or reject what the consumer disputes.", ["Billing Manager", "General Manager"]),
	"corrections.senior": ("Corrections", "Corrections that need the General Manager", "Changes reserved for the most senior manager.",
	                       ["General Manager"]),
	# recovery
	"recovery.run": ("Recovery", "Run recovery stages", "Prepare and issue notices, assign cases, create disconnection requests.",
	                 ["Recovery Officer", "Billing Manager", "General Manager", "Society Admin"]),
	"recovery.approve": ("Recovery", "Approve recovery steps", "Approve the recovery steps that need a second person.", ["Billing Manager", "General Manager"]),
	"recovery.view": ("Recovery", "See defaulters and recovery", "The defaulters and recovery screen and its figures.",
	                  [*RECOVERY, "Recovery Officer", "Billing Manager", "General Manager", "Society Admin", "Billing Officer"]),
	"recovery.handle_case": ("Recovery", "Be assigned recovery cases", "Recovery cases can be assigned to this user.", ["Recovery Officer"]),
	"ra.work": ("Recovery", "Work revenue assurance", "Run the revenue assurance checks.", ["Billing Manager", "General Manager", "Recovery Officer"]),
	"ra.manage": ("Recovery", "Close revenue assurance findings", "Decide and close findings.", ["Billing Manager", "General Manager"]),
	# messages
	"messages.send": ("Messages", "Send messages and broadcasts", "Send a broadcast, or the queued messages now.",
	                  ["Billing Manager", "General Manager", "Society Admin", "Recovery Officer", "Accounts Officer"]),
	"templates.manage": ("Messages", "Set up message templates", "Create the society's standard notification templates.", ["Society Admin"]),
	# period control and accounting
	"period.close": ("Period control", "Close a month or year", "Close a billing month, or the year end.",
	                 ["Billing Manager", "General Manager", "Accounts Officer"]),
	"period.reopen": ("Period control", "Reopen a closed month", "Reopen a month someone closed.", ["General Manager"]),
	"gl.export": ("Accounting", "Export journals", "Export GL journals for the accounting system.", ["Accounts Officer", "Billing Manager", "General Manager"]),
	"gl.view": ("Accounting", "See journals and reconciliation", "GL journals and the receivables reconciliation.",
	            ["Accounts Officer", "Billing Manager", "General Manager", "Billing Auditor", "Management"]),
	# access and setup
	"approvals.delegate_others": ("Access", "Delegate approvals for others", "Set up an approval delegation for another user.",
	                              ["General Manager", "Society Admin"]),
	"users.manage": ("Access", "Manage the society's users", "Create, edit and disable users of one's own societies and give them roles.",
	                 ["Society Admin", "Access Manager"]),
	"rights.manage": ("Access", "Manage action rights", "Tick which roles may do each action on the Role Rights screen.",
	                  ["Society Admin", "Access Manager"]),
	"security.view": ("Access", "See the sign-in security settings", "Password and two-factor policy status.",
	                  ["Billing Auditor", "General Manager", "Society Admin"]),
	"licence.manage": ("Licensing", "Manage SoftVerx licence billing", "Licence clients, usage, invoices and payments (control site).",
	                   ["Licence Manager"]),
	"licence.settings": ("Licensing", "Set this site's licence key", "The site's licence key and usage reporting.", ["Society Admin"]),
}

CACHE_KEY = "ub-action-rights"
ALWAYS = {"System Manager"}


def app_roles() -> list[str]:
	"""The roles this app works with: the ones it installs, and any role the society creates itself (not Frappe's or ERPNext's own)."""
	import json  # noqa: PLC0415
	import os  # noqa: PLC0415

	path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "setup_data", "roles.json")
	ours = {r["name"] for r in json.load(open(path))}
	custom = set(frappe.get_all("Role", filters={"is_custom": 1}, pluck="name"))
	names = frappe.get_all("Role", filters={"disabled": 0, "desk_access": 1}, pluck="name", order_by="name")
	return [r for r in names if (r in ours or r in custom) and not r.startswith("_Test")
	        and r not in ("Administrator", "System Manager", "Guest", "All", "Desk User")]


def clear_cache():
	frappe.cache.delete_value(CACHE_KEY)


def _table() -> dict[str, set[str]] | None:
	"""{action: roles} for every Action Right record (an action with no roles ticked maps to an empty set); None before the
	DocType exists."""
	table = frappe.cache.get_value(CACHE_KEY)
	if table is None:
		if not frappe.db.table_exists("Action Right"):
			return None
		table = {name: [] for name in frappe.get_all("Action Right", pluck="name")}
		for r in frappe.get_all("Action Right Role", filters={"parenttype": "Action Right"}, fields=["parent", "role"]):
			table.setdefault(r.parent, []).append(r.role)
		frappe.cache.set_value(CACHE_KEY, table)
	return {k: set(v) for k, v in table.items()}


def roles_for(action: str) -> set[str]:
	"""The roles allowed this action: its Action Right record, or the starting roles until the record exists."""
	if action not in ACTIONS:
		raise KeyError(f"unknown action right {action}")
	table = _table() or {}
	return table[action] if action in table else set(ACTIONS[action][3])


def can(action: str, user: str | None = None, roles=None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator" and roles is None:
		return True
	have = set(roles) if roles is not None else set(frappe.get_roles(user))
	return bool(have & (roles_for(action) | ALWAYS))


def require(action: str, message: str | None = None):
	if not can(action):
		raise frappe.PermissionError(message or _("You don't have the right to {0}.").format(_(ACTIONS[action][1]).lower()))


def mine(user: str | None = None) -> list[str]:
	"""The actions this user may do, for the browser (frappe.boot.ub_rights)."""
	return sorted(a for a in ACTIONS if can(a, user))


def ensure_defaults():
	"""Write the starting roles for actions that have no record yet (after install and migrate); never change an existing one."""
	if not frappe.db.table_exists("Action Right"):
		return 0
	existing = set(frappe.get_all("Action Right", pluck="name"))
	roles = set(frappe.get_all("Role", pluck="name"))
	made = 0
	for code, (area, label, description, starting) in ACTIONS.items():
		if code in existing:
			# keep the society's roles; only the wording follows the app
			frappe.db.set_value("Action Right", code, {"label": label, "area": area, "description": description}, update_modified=False)
			continue
		doc = frappe.get_doc({"doctype": "Action Right", "action": code, "label": label, "area": area, "description": description,
		                      "roles": [{"role": r} for r in dict.fromkeys(starting) if r in roles]})
		doc.insert(ignore_permissions=True)
		made += 1
	clear_cache()
	return made


# ---------------------------------------------------------------- the Role Rights screen
@frappe.whitelist()
def matrix():
	"""Actions down the side, roles across, ticks where a role is allowed (page ub-rights)."""
	if not can("rights.manage") and not can("users.manage"):
		raise frappe.PermissionError(_("You can't see the action rights."))
	shown = app_roles()
	return {
		"roles": shown,
		"actions": [{"action": a, "area": v[0], "label": _(v[1]), "description": _(v[2]), "roles": sorted(roles_for(a))}
		            for a, v in ACTIONS.items()],
		"can_edit": can("rights.manage"),
	}


@frappe.whitelist(methods=["POST"])
def set_right(action: str, role: str, allowed: int):
	require("rights.manage")
	if action not in ACTIONS:
		frappe.throw(_("Unknown action {0}.").format(action))
	if role in ("Administrator", "System Manager", "Guest", "All"):
		frappe.throw(_("Role {0} can't be changed here.").format(role))
	if not frappe.db.exists("Role", role):
		frappe.throw(_("Role {0} not found.").format(role))
	ensure_defaults()
	doc = frappe.get_doc("Action Right", action)
	have = [r.role for r in doc.roles]
	if int(allowed) and role not in have:
		doc.append("roles", {"role": role})
	elif not int(allowed) and role in have:
		doc.set("roles", [r for r in doc.roles if r.role != role])
	doc.save(ignore_permissions=True)  # the right was checked above
	return sorted(r.role for r in doc.roles)
