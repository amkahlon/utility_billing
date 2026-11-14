# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Sign-in security (FS-ACC-09): strong passwords, two-factor sign-in for managers and administrators, and a lock after repeated failed
sign-ins. Frappe enforces all three; this module shows where a site stands and applies the recommended policy when a System Manager asks.
It is never applied by itself: two-factor sign-in needs each manager to set up an authenticator app first."""

import frappe
from frappe import _
from frappe.utils import cint

TWO_FACTOR_ROLES = ("System Manager", "Society Admin", "General Manager", "Billing Manager", "Management", "Accounts Officer")
POLICY = {"enable_password_policy": 1, "minimum_password_score": "3", "allow_consecutive_login_attempts": 5, "allow_login_after_fail": 900,
          "enable_two_factor_auth": 1, "two_factor_method": "OTP App", "otp_issuer_name": "Utility Billing"}


def status() -> list[dict]:
	s = frappe.get_single("System Settings")
	roles_on = set(frappe.get_all("Role", filters={"name": ["in", list(TWO_FACTOR_ROLES)], "two_factor_auth": 1}, pluck="name"))
	rows = [
		(_("Password policy on"), cint(s.enable_password_policy) == 1, _("on") if cint(s.enable_password_policy) else _("off")),
		(_("Strong passwords (score 3 or more)"), cint(s.minimum_password_score) >= 3, _("score {0}").format(s.minimum_password_score)),
		(_("Lock after 5 or fewer failed sign-ins"), 0 < cint(s.allow_consecutive_login_attempts) <= 5,
		 _("after {0} attempts, for {1} seconds").format(s.allow_consecutive_login_attempts, s.allow_login_after_fail)),
		(_("Two-factor sign-in on"), cint(s.enable_two_factor_auth) == 1, _("{0}").format(s.two_factor_method if cint(s.enable_two_factor_auth) else _("off"))),
		(_("Two-factor for managers and administrators"), roles_on == set(TWO_FACTOR_ROLES),
		 ", ".join(sorted(roles_on)) or _("no role")),
	]
	return [{"check": c, "ok": bool(ok), "now": now} for c, ok, now in rows]


@frappe.whitelist()
def get_status():
	if "System Manager" not in frappe.get_roles() and not set(frappe.get_roles()) & {"Billing Auditor", "General Manager", "Society Admin"}:
		raise frappe.PermissionError(_("You can't see the security settings."))
	return status()


@frappe.whitelist(methods=["POST"])
def apply_policy():
	"""Apply the recommended policy (System Manager only)."""
	if "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError(_("Only a System Manager changes sign-in security."))
	for key, value in POLICY.items():  # only these settings; the rest of System Settings is left as it is
		frappe.db.set_single_value("System Settings", key, value)
	frappe.clear_cache()
	for r in TWO_FACTOR_ROLES:
		frappe.db.set_value("Role", r, "two_factor_auth", 1)
	return status()
