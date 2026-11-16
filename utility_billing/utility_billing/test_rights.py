# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 25: permissions the society decides (action rights, Role Rights, Society Users, install-once roles and workflows)."""

import frappe

from utility_billing import boot, install
from utility_billing.utility_billing import approval, cashdesk, rights, users
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import CASHIER, GENERAL_MANAGER

ADMIN = "tst.society.admin@example.com"
OTHER = "TST-OTHER"


class IntegrationTestRights(BillingCase):
	def setUp(self):
		super().setUp()
		install.ensure_roles_and_workflows()
		rights.clear_cache()

	def tearDown(self):
		rights.clear_cache()
		super().tearDown()

	def society_admin(self, societies=(SOC,)):
		if not frappe.db.exists("User", ADMIN):
			u = frappe.get_doc({"doctype": "User", "email": ADMIN, "first_name": "Society Admin", "send_welcome_email": 0,
			                    "roles": [{"role": "Society Admin"}]})
			u.flags.no_welcome_mail = True
			u.insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "User Access", "user": ADMIN, "societies": [{"society": s} for s in societies]}).insert(ignore_permissions=True)
		return ADMIN

	# ---- action rights
	def test_every_action_has_a_record_with_its_starting_roles_and_reruns_keep_changes(self):
		self.assertEqual(set(frappe.get_all("Action Right", pluck="name")) >= set(rights.ACTIONS), True)
		self.assertIn("Cashier", rights.roles_for("cash.counter"))
		doc = frappe.get_doc("Action Right", "cash.counter")
		doc.set("roles", [{"role": "Accounts Officer"}])
		doc.save()
		install.ensure_roles_and_workflows()  # an update must not put Cashier back
		self.assertEqual(rights.roles_for("cash.counter"), {"Accounts Officer"})
		frappe.get_doc({"doctype": "Action Right", "action": "old.action", "label": "Gone", "roles": [{"role": "Cashier"}]}).insert()
		rights.ensure_defaults()  # an action the app no longer has is removed
		self.assertFalse(frappe.db.exists("Action Right", "old.action"))

	def test_what_the_society_ticks_decides_who_may_act(self):
		frappe.set_user(CASHIER)
		self.assertTrue(rights.can("cash.counter"))
		cashdesk.state()  # allowed
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Action Right", "cash.counter")
		doc.set("roles", [r for r in doc.roles if r.role != "Cashier"])
		doc.save()
		frappe.set_user(CASHIER)
		self.assertFalse(rights.can("cash.counter"))
		self.assertRaises(frappe.PermissionError, cashdesk.state)
		self.assertNotIn("cash.counter", rights.mine())
		frappe.set_user("Administrator")
		self.assertTrue(rights.can("cash.counter"))  # Administrator and System Manager always may

	def test_only_holders_of_the_right_change_rights(self):
		frappe.set_user(CASHIER)
		self.assertRaises(frappe.PermissionError, rights.set_right, "cash.counter", "Billing Officer", 1)
		frappe.set_user("Administrator")
		self.assertIn("Billing Officer", rights.set_right("cash.counter", "Billing Officer", 1))
		self.assertTrue(rights.can("cash.counter", roles=["Billing Officer"]))
		self.assertRaises(frappe.ValidationError, rights.set_right, "cash.counter", "System Manager", 0)
		m = rights.matrix()
		self.assertNotIn("System Manager", m["roles"])
		self.assertEqual(len(m["actions"]), len(rights.ACTIONS))

	def test_landing_page_follows_the_dashboard_right(self):
		self.assertEqual(boot.home_page(["Billing Officer"]), "ub-dashboard")
		doc = frappe.get_doc("Action Right", "dashboard.view")
		doc.set("roles", [r for r in doc.roles if r.role != "Billing Officer"])
		doc.save()
		self.assertEqual(boot.home_page(["Billing Officer"]), "List/Connection")

	# ---- approvals and install-once workflows
	def test_workflows_and_roles_are_created_once_and_then_left_alone(self):
		hooks = frappe.get_hooks("fixtures")
		self.assertFalse([f for f in hooks if (f.get("dt") if isinstance(f, dict) else f) in ("Workflow", "Role")])
		self.assertTrue(frappe.db.exists("Role", "Access Manager"))
		wf = frappe.get_doc("Workflow", "Bill Run Approval")
		wf.set("transitions", [t for t in wf.transitions if t.allowed != "General Manager"])
		wf.save()
		install.ensure_roles_and_workflows()
		self.assertNotIn("General Manager", [t.allowed for t in frappe.get_doc("Workflow", "Bill Run Approval").transitions])

	def test_amount_led_approvers_are_the_societys_levels(self):
		# who approves an amount-led document is set on the Society (Approval Levels by Amount); without levels, the app's default
		dt, field = next(iter(approval.AMOUNT_FIELD.items()))
		doc = frappe.new_doc(dt)
		doc.society = SOC
		doc.set(field, 100)
		self.assertEqual(approval.required_roles(doc), [approval.DEFAULT_APPROVER[dt]])
		soc = frappe.get_doc("Society", SOC)
		soc.append("approval_levels", {"transaction": dt, "approver_role": "Billing Manager", "up_to_amount": 0})
		soc.save()
		frappe.clear_document_cache("Society", SOC)
		self.assertEqual(approval.required_roles(doc), ["Billing Manager"])

	# ---- Society Users
	def test_a_society_admin_manages_the_users_of_their_own_society(self):
		if not frappe.db.exists("Society", OTHER):
			frappe.get_doc({"doctype": "Society", "society_name": "Other Test Society", "short_name": OTHER}).insert()
		admin = self.society_admin()
		frappe.set_user(admin)
		data = users.save_user({"email": "new.cashier@example.com", "full_name": "New Cashier", "roles": ["Cashier"], "societies": [SOC]})
		# the admin sees only users limited to their society (themselves included), never the managers of other societies
		listed = {u["name"]: u for u in data["users"]}
		self.assertTrue({"new.cashier@example.com", ADMIN} <= set(listed))
		self.assertNotIn(GENERAL_MANAGER, listed)
		self.assertTrue(all(set(u["societies"]) <= {SOC} for u in listed.values()))
		self.assertIn("Cashier", frappe.get_roles("new.cashier@example.com"))
		self.assertEqual(frappe.get_all("Access Society", filters={"parent": "new.cashier@example.com"}, pluck="society"), [SOC])
		self.assertNotIn("System Manager", data["roles"])
		self.assertIn("Access Manager", data["roles"])  # access management is a role the admin can give
		self.assertRaises(frappe.ValidationError, users.save_user, {"email": "x@example.com", "roles": ["System Manager"], "societies": [SOC]})
		self.assertRaises(frappe.PermissionError, users.save_user, {"email": "y@example.com", "roles": ["Cashier"], "societies": [OTHER]})
		self.assertRaises(frappe.PermissionError, users.save_user, {"email": GENERAL_MANAGER, "roles": ["Cashier"], "societies": [SOC]})
		users.save_user({"email": "new.cashier@example.com", "full_name": "New Cashier", "roles": ["Access Manager"], "societies": [SOC], "enabled": 0})
		self.assertEqual(frappe.db.get_value("User", "new.cashier@example.com", "enabled"), 0)
		self.assertEqual(set(frappe.get_roles("new.cashier@example.com")) & {"Cashier", "Access Manager"}, {"Access Manager"})
		frappe.set_user("Administrator")

	def test_managing_users_needs_the_right(self):
		frappe.set_user(CASHIER)
		self.assertRaises(frappe.PermissionError, users.users)
		frappe.set_user("Administrator")
