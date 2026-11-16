# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Fixes from the user's first bug report on ub.localhost (Bug Report.docx, 2026-11-16)."""

import frappe
from frappe.utils import nowdate

from utility_billing import boot, pdf
from utility_billing.utility_billing import approval
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER


class IntegrationTestBugReport(BillingCase):
	def deposit(self, amount):
		return frappe.get_doc({"doctype": "Security Deposit", "connection": self.conn, "deposit_type": "Received", "amount": amount,
		                       "posting_date": nowdate()}).insert()

	def test_meter_readers_and_cashiers_open_a_page_they_may_see(self):
		self.assertEqual(boot.home_page(["Meter Reader"]), "ub-readings")
		self.assertEqual(boot.home_page(["Cashier"]), "ub-cash")
		self.assertEqual(boot.home_page(["Cashier", "Billing Manager"]), "ub-dashboard")
		self.assertEqual(boot.home_page(["Billing Officer"]), "ub-dashboard")
		roles = set(frappe.get_all("Has Role", filters={"parenttype": "Page", "parent": "ub-dashboard"}, pluck="role"))
		self.assertTrue({"Meter Reader", "Cashier"} <= roles)

	def test_a_workflow_action_on_ticked_records_says_what_it_did(self):
		if not approval.workflow_active("Security Deposit"):
			self.skipTest("no Security Deposit workflow on this site")
		frappe.set_user(BILLING_MANAGER)
		a, b = self.deposit(100), self.deposit(200)
		r = approval.bulk_action("Security Deposit", [a.name, b.name], "Submit for Approval")
		self.assertEqual((sorted(r["done"]), r["failed"]), (sorted([a.name, b.name]), []))
		self.assertEqual(frappe.db.get_value("Security Deposit", a.name, "workflow_state"), "Pending Approval")
		# the same user can't approve what they submitted: each record says why
		r = approval.bulk_action("Security Deposit", frappe.as_json([a.name]), "Approve")
		self.assertEqual(r["done"], [])
		self.assertEqual(r["failed"][0]["name"], a.name)
		self.assertTrue(r["failed"][0]["reason"])
		frappe.set_user("Administrator")

	def test_the_route_is_chosen_from_the_reading_routes_and_kept_in_step(self):
		r1 = frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": "BR-1"}).insert()
		r2 = frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": "BR-2"}).insert()
		conn = frappe.get_doc("Connection", self.conn)
		conn.route = "BR-1"
		conn.save()
		self.assertEqual([s.connection for s in frappe.get_doc("Reading Route", r1.name).stops], [self.conn])
		conn.reload()
		conn.route = "BR-2"
		conn.save()
		self.assertEqual(frappe.get_doc("Reading Route", r1.name).stops, [])
		self.assertEqual([s.connection for s in frappe.get_doc("Reading Route", r2.name).stops], [self.conn])
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "route"), "BR-2")
		conn.reload()
		conn.route = "NO-SUCH"
		self.assertRaises(frappe.ValidationError, conn.save)
		conn.reload()
		conn.route = ""
		conn.save()
		self.assertEqual(frappe.get_doc("Reading Route", r2.name).stops, [])

	def test_a_route_brings_its_usual_reader_into_the_cycle(self):
		route = frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": "BR-3",
		                        "reader": BILLING_OFFICER}).insert()
		cycle = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-12-01",
		                        "route_assignments": [{"route": route.name}]}).insert()
		self.assertEqual(cycle.route_assignments[0].reader, BILLING_OFFICER)

	def test_report_pdf_leaves_out_the_footer_unpatched_wkhtmltopdf_cannot_print(self):
		html = '<div id="header-html">h</div><table><tr><td>Rs 1,000</td></tr></table><div id="footer-html" class="visible-pdf">f</div>'
		out = pdf.without_header_footer(html)
		self.assertNotIn("footer-html", out)
		self.assertNotIn("header-html", out)
		self.assertIn("Rs 1,000", out)
		self.assertEqual(frappe.get_hooks("override_whitelisted_methods")["frappe.utils.print_format.report_to_pdf"],
		                 ["utility_billing.pdf.report_to_pdf"])

	def test_only_one_check_meter_field_shows_on_feeders_and_transformers(self):
		for dt in ("Feeder", "Transformer"):
			meta = frappe.get_meta(dt)
			self.assertTrue(meta.get_field("check_meter_no").hidden)
			self.assertFalse(meta.get_field("check_meter").hidden)

	def test_users_and_access_are_in_the_menu(self):
		items = frappe.get_all("Sidebar Item", filters={"parent": "Utility Management System"}, fields=["label", "link_to"], order_by="idx")
		labels = [i.label for i in items]
		self.assertIn("Users and Access", labels)
		self.assertTrue({"user", "role", "permission-manager"} <= {(i.link_to or "").lower() for i in items})
