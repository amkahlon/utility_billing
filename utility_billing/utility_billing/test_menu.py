# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Each role's sidebar lists the screens it works in (user, 2026-11-21: "trim each role's sidebar to its own screens"; increment 44)."""

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing import boot, menu


def link(link_type, link_to, child=1):
	return {"type": "Link", "label": link_to, "link_type": link_type, "link_to": link_to, "child": child}


def section(label):
	return {"type": "Section Break", "label": label, "child": 0}


ITEMS = [
	link("Page", "ub-dashboard", child=0),
	section("Users and Access"), link("Page", "ub-users"), link("DocType", "User"),
	section("Consumers"), link("Page", "ub-consumer-360"), link("DocType", "Connection"), link("DocType", "Consumer"),
	section("Billing"), link("DocType", "Bill Run"), link("DocType", "Utility Bill"),
	section("Payments"), link("Page", "ub-cash"), link("DocType", "Payment Receipt"), link("DocType", "Settlement File"),
	section("Corrections"), link("DocType", "Bill Revision"), link("DocType", "Court Hold"),
	section("Reports"), link("Report", "Collections"),
]


def labels(roles):
	return [(i["label"] if i["type"] == "Section Break" else i["link_to"]) for i in menu.trim(ITEMS, set(roles))]


class IntegrationTestMenu(IntegrationTestCase):
	def test_a_cashier_sees_the_counter_and_finding_an_account(self):
		# Frappe has already left out the pages and reports a role may not open; here only DocType lists and the dashboard are judged
		self.assertEqual(labels(["Cashier"]), ["Users and Access", "ub-users", "Consumers", "ub-consumer-360", "Connection",
		                                       "Payments", "ub-cash", "Payment Receipt", "Reports", "Collections"])

	def test_a_billing_officer_sees_billing_not_payments_or_users(self):
		got = labels(["Billing Officer"])
		self.assertIn("ub-dashboard", got)
		for name in ("Bill Run", "Utility Bill", "Consumer", "Bill Revision", "Court Hold"):
			self.assertIn(name, got)
		for name in ("User", "Payment Receipt", "Settlement File"):
			self.assertNotIn(name, got)

	def test_a_recovery_officer_sees_the_holds_and_plans_but_not_revisions(self):
		got = labels(["Recovery Officer"])
		self.assertIn("Court Hold", got)
		self.assertNotIn("Bill Revision", got)
		self.assertNotIn("Billing", got)  # a section left empty is dropped

	def test_managers_and_system_managers_see_everything_they_did(self):
		self.assertEqual(menu.trim(ITEMS, {"System Manager"}), ITEMS)
		self.assertNotIn("User", labels(["Billing Manager"]))  # users are the General Manager's and Society Admin's
		self.assertIn("User", labels(["General Manager"]))

	def test_every_section_and_extra_names_a_real_sidebar_section_and_doctype(self):
		sb = frappe.get_doc("Sidebar", menu.SIDEBAR)
		sections = {i.label for i in sb.items if i.type == "Section Break"}
		self.assertFalse(set(menu.SECTION_ROLES) - sections, "a SECTION_ROLES key isn't a sidebar section")
		self.assertFalse((sections - {"Reports"}) - set(menu.SECTION_ROLES), "a sidebar section has no roles")
		for doctype in menu.EXTRA_ROLES:
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)

	def test_the_boot_hook_trims_the_apps_sidebar(self):
		info = frappe._dict(module_sidebars={menu.SIDEBAR: {"items": list(ITEMS)}, "Other": {"items": list(ITEMS)}})
		menu.apply(info, roles=["Cashier"])
		self.assertEqual(len(info.module_sidebars[menu.SIDEBAR]["items"]), 10)
		self.assertEqual(len(info.module_sidebars["Other"]["items"]), len(ITEMS))  # only the app's own sidebar
		self.assertTrue(callable(boot.boot_session))
