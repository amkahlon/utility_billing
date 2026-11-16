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

	def test_pdfs_work_without_chromium_libraries_or_patched_wkhtmltopdf(self):
		pdf.apply()
		from frappe.utils import pdf as fpdf
		from frappe.utils import print_utils

		self.assertEqual(fpdf.prepare_options.__module__, "utility_billing.pdf")
		html = '<link href="http://no-such-host.localhost/assets/frappe/dist/css/print.bundle.css" rel="stylesheet">' 		       '<link href="/assets/../../etc/passwd.css" rel="stylesheet"><p>Rs 1,000</p>'
		import glob, os
		bundle = glob.glob(os.path.join(frappe.utils.get_bench_path(), "sites", "assets", "frappe", "dist", "css", "print.bundle.*.css"))
		if bundle:
			html = html.replace("print.bundle.css", os.path.basename(bundle[0]))
			out = pdf.inline_stylesheets(html)
			self.assertNotIn("no-such-host", out)
			self.assertIn("<style>", out)
		self.assertIn("etc/passwd.css", pdf.inline_stylesheets(html))  # nothing outside sites/assets is read
		if not pdf.chrome_works():
			self.assertNotEqual(print_utils.resolve_pdf_generator(None, "chrome"), "chrome")
		# a record printed to PDF the way the desk does it
		from frappe.utils.print_format import download_pdf

		frappe.local.response = frappe._dict()
		download_pdf("Connection", self.conn)
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		self.assertNotIn("header-html", pdf.without_header_footer('<div id="header-html">h</div><p>x</p>'))

	def test_the_surcharge_has_its_own_line_on_the_bill(self):
		for template in ("utility_bill.html", "combined_bill.html"):
			path = frappe.get_app_path("utility_billing", "templates", "bill", template)
			text = open(path).read()
			self.assertIn('class="surcharge"', text, template)
			self.assertNotIn("incl. surcharge", text)

	def test_block_sector_and_office_boxes_offer_saved_values(self):
		from utility_billing.utility_billing import choices

		frappe.db.set_value("Property", self.prop, {"block": "N Block", "sector": "Sector 2"})
		soc = frappe.get_doc("Society", SOC)
		soc.append("offices", {"office_name": "Main Office", "office_type": "Office"})
		soc.append("offices", {"office_name": "Q Block", "office_type": "Block"})
		soc.save()
		self.assertEqual(choices.values("block", SOC), ["N Block", "Q Block"])
		self.assertEqual(choices.values("sector", SOC), ["Sector 2"])
		self.assertEqual(choices.values("office", SOC), ["Main Office"])
		self.assertRaises(frappe.ValidationError, choices.values, "users")
		for dt, field in (("Property", "block"), ("Property", "sector"), ("Broadcast", "block"), ("Bulk Adjustment", "block"),
		                  ("Recovery Run", "block"), ("Access Block", "block"), ("Cash Counter", "office")):
			self.assertEqual(frappe.get_meta(dt).get_field(field).fieldtype, "Autocomplete", f"{dt}.{field}")

	def test_a_cashier_day_opens_at_the_cashiers_usual_counter(self):
		from utility_billing.utility_billing import choices
		from utility_billing.utility_billing.testing import CASHIER

		if not frappe.db.exists("Cash Counter", "TB1"):
			frappe.get_doc({"doctype": "Cash Counter", "counter_code": "TB1", "counter_name": "Test counter", "society": SOC}).insert()
		self.assertIsNone(choices.default_counter("nobody@example.com"))
		frappe.set_user(CASHIER)
		first = frappe.get_doc({"doctype": "Cashier Day", "cash_counter": "TB1", "opening_float": 0}).insert()
		self.assertEqual(choices.default_counter(CASHIER), "TB1")
		frappe.set_user("Administrator")
		self.assertEqual(first.cash_counter, "TB1")

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

	def test_the_general_manager_approves_bill_runs_too(self):
		from utility_billing.utility_billing.testing import GENERAL_MANAGER, act

		self.month_reading("2026-08-01", 2830)
		self.fpa("2026-08-01", 0)
		run = self.make_run("2026-08-01", "2026-09-01")
		self.assertEqual(run.bill_count, 1)
		act(run, "Submit for Approval", BILLING_OFFICER)
		frappe.set_user(GENERAL_MANAGER)
		from frappe.model.workflow import get_transitions

		self.assertEqual(sorted(t.action for t in get_transitions(frappe.get_doc("Bill Run", run.name))), ["Approve", "Reject"])
		frappe.set_user("Administrator")
		act(run, "Approve", GENERAL_MANAGER)
		run.reload()
		self.assertEqual((run.docstatus, run.workflow_state), (1, "Approved"))

	def test_a_minimum_is_the_tariffs_only(self):
		self.assertIsNone(frappe.get_meta("Society").get_field("minimum_bill"))
		self.assertIsNotNone(frappe.get_meta("Tariff").get_field("minimum_charge"))

	def test_a_whole_bill_minimum_set_on_the_tariff_tops_up_the_bill(self):
		tariff = frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": self.cat, "docstatus": 1})
		frappe.db.set_value("Tariff", tariff, {"minimum_charge": 70000, "minimum_applies_to": "Whole current bill"})
		frappe.clear_document_cache("Tariff", tariff)
		self.month_reading("2026-08-01", 2830)  # 831 units: 58,170 energy + 2,700 fixed = 60,870
		self.fpa("2026-08-01", 0)
		bill = self.bill(self.make_run("2026-08-01", "2026-09-01"))
		self.assertEqual((bill.lines[-1].label, round(bill.lines[-1].amount)), ("Minimum bill adjustment", 9130))
		self.assertEqual(round(bill.current_amount), 70000)
