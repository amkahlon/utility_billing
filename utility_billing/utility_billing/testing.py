# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Shared helpers for tests (not a test module itself)."""

import frappe
from frappe.model.workflow import apply_workflow

BILLING_MANAGER = "tst.billing.manager@example.com"
BILLING_MANAGER_2 = "tst.billing.manager2@example.com"
GENERAL_MANAGER = "tst.general.manager@example.com"
BILLING_OFFICER = "tst.billing.officer@example.com"
BM_AND_GM = "tst.bm.gm@example.com"

USERS = {
	BILLING_MANAGER: ["Billing Manager"],
	BILLING_MANAGER_2: ["Billing Manager"],
	GENERAL_MANAGER: ["General Manager"],
	BILLING_OFFICER: ["Billing Officer"],
	BM_AND_GM: ["Billing Manager", "General Manager"],
}


def make_users():
	for email, roles in USERS.items():
		if frappe.db.exists("User", email):
			continue
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)


def act(doc, action: str, user: str):
	"""Apply a workflow action as `user`, then switch back to Administrator."""
	frappe.set_user(user)
	try:
		doc.reload()
		return apply_workflow(doc, action)
	finally:
		frappe.set_user("Administrator")


def approve(doc, maker=BILLING_MANAGER, approver=GENERAL_MANAGER):
	act(doc, "Submit for Approval", maker)
	act(doc, "Approve", approver)
	doc.reload()
	return doc
