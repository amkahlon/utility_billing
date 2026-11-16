# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society Users (page ub-users, increment 25; user's requirement 2026-11-16: "Society Admin should also be able to manage users of
their own society. Access management role may also be defined and assigned to any user by the admin").

Whoever has the action right "users.manage" (by default Society Admin and Access Manager) lists, creates, edits and disables
users, gives them roles and limits them to societies, counters and blocks. A manager limited to some societies sees and manages
only users limited to those same societies; site-level roles (System Manager and the like) are never given or taken here, and a
user holding one can't be edited here. The User records are written by this module, which enforces these limits, so the
managers need no rights on User itself."""

import frappe
from frappe import _
from frappe.utils import cint, validate_email_address

from utility_billing.utility_billing import access, rights

SITE_ROLES = {"Administrator", "System Manager", "Guest", "All", "Desk User", "Licence Manager", "API Integration"}


def _scope() -> set[str] | None:
	"""The societies the manager may manage users of; None for all."""
	rights.require("users.manage")
	mine = access.societies_of()
	if mine is None and "System Manager" not in frappe.get_roles() and frappe.session.user != "Administrator":
		ua = frappe.db.get_value("User Access", frappe.session.user, "all_societies")
		if not cint(ua):
			frappe.throw(_("Your access isn't limited to any society yet, so you can't manage society users. Ask the System Manager to set "
			               "your Society and Block Access."), frappe.PermissionError)
	return set(mine) if mine is not None else None


def assignable_roles() -> list[str]:
	"""The app's roles and the society's own, without the site-level ones."""
	return [r for r in rights.app_roles() if r not in SITE_ROLES]


def _manageable(user: str, scope) -> bool:
	if user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	if roles & (SITE_ROLES - {"All", "Guest", "Desk User"}) and "System Manager" not in frappe.get_roles():
		return False
	if scope is None:
		return True
	theirs = access.societies_of(user)
	return theirs is not None and set(theirs) <= scope


@frappe.whitelist()
def users():
	scope = _scope()
	rows = frappe.get_all("User", filters={"user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
	                      fields=["name", "full_name", "enabled", "last_login"], order_by="full_name")
	assignable = set(assignable_roles())
	out = []
	for u in rows:
		if not _manageable(u.name, scope):
			continue
		ua = frappe.get_doc("User Access", u.name) if frappe.db.exists("User Access", u.name) else None
		out.append({**u, "roles": sorted(set(frappe.get_roles(u.name)) & assignable),
		            "societies": [r.society for r in ua.societies] if ua else [],
		            "blocks": [{"society": r.society, "block": r.block} for r in ua.blocks] if ua else [],
		            "counters": [r.cash_counter for r in ua.counters] if ua else []})
	societies = frappe.get_all("Society", fields=["name", "society_name"], order_by="society_name")
	return {
		"users": out,
		"roles": sorted(assignable),
		"societies": [s for s in societies if scope is None or s.name in scope],
		"counters": frappe.get_all("Cash Counter", filters={"enabled": 1, **({"society": ["in", list(scope)]} if scope else {})},
		                           fields=["name", "counter_name", "society"], order_by="name"),
		"scoped": scope is not None,
	}


@frappe.whitelist(methods=["POST"])
def save_user(data):
	"""Create or change one user: name, e-mail, enabled, roles, societies, blocks and counters."""
	scope = _scope()
	d = frappe.parse_json(data)
	email = (d.get("email") or "").strip().lower()
	if not validate_email_address(email):
		frappe.throw(_("Enter a valid e-mail address."))
	roles = set(d.get("roles") or [])
	bad = roles - set(assignable_roles())
	if bad:
		frappe.throw(_("These roles can't be given here: {0}.").format(", ".join(sorted(bad))))
	societies = set(d.get("societies") or [])
	if not societies:
		frappe.throw(_("Choose at least one society the user works for."))
	if scope is not None and not societies <= scope:
		frappe.throw(_("You can only give access to your own societies."), frappe.PermissionError)
	counters = [c for c in d.get("counters") or [] if c]
	for c in counters:
		if frappe.db.get_value("Cash Counter", c, "society") not in societies:
			frappe.throw(_("Cash counter {0} isn't in the user's societies.").format(c))
	blocks = [b for b in d.get("blocks") or [] if b.get("block")]
	for b in blocks:
		if b.get("society") not in societies:
			frappe.throw(_("Block {0} must be in one of the user's societies.").format(b.get("block")))

	if frappe.db.exists("User", email):
		if not _manageable(email, scope):
			frappe.throw(_("You can't change user {0}: they have access outside your societies or a site-level role.").format(email),
			             frappe.PermissionError)
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc({"doctype": "User", "email": email, "send_welcome_email": 0, "user_type": "System User"})
	user.first_name = (d.get("full_name") or email.split("@")[0]).strip()
	user.enabled = 1 if cint(d.get("enabled", 1)) else 0
	keep = [r.role for r in user.roles if r.role not in set(assignable_roles())]  # roles not managed here stay as they are
	user.set("roles", [{"role": r} for r in [*keep, *sorted(roles)]])
	user.flags.ignore_permissions = True  # the limits above are this screen's permission check
	user.save() if not user.is_new() else user.insert()

	ua = frappe.get_doc("User Access", email) if frappe.db.exists("User Access", email) else frappe.new_doc("User Access")
	ua.user = email
	ua.all_societies = 0
	ua.set("societies", [{"society": s} for s in sorted(societies)])
	ua.set("counters", [{"cash_counter": c} for c in counters])
	ua.set("blocks", [{"society": b["society"], "block": b["block"]} for b in blocks])
	ua.flags.ignore_permissions = True
	ua.save()
	return users()
