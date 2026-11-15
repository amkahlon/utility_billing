# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Client setup (increment 21 part G; FS-CLT-07, 09, 10): signatories, branding history, the onboarding checklist.
House N 2308 (spec test case 1): the October bill is issued on 01-11-2026."""

import frappe
from frappe.utils import nowdate

from utility_billing.utility_billing import account_print, bill_print, branding, onboarding
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER


class IntegrationTestClientSetup(BillingCase):
	def society(self, **kw):
		s = frappe.get_doc("Society", SOC)
		s.update(kw)
		return s

	def october_bill(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.bill(self.post(self.make_run("2026-10-01", "2026-11-01")))

	# ---- branding history (CLT-09)
	def test_the_branding_in_force_on_a_day(self):
		s = self.society(brand_colour="#1F5F8B", footer_text="Old footer")
		s.set("branding_versions", [{"effective_from": "2026-10-01", "brand_colour": "#AA0000"},
		                            {"effective_from": "2026-12-01", "brand_colour": "#00AA00", "footer_text": "New footer"}])
		s.save()
		self.assertEqual((branding.effective(SOC, "2026-09-30")["colour"], branding.effective(SOC, "2026-09-30")["footer"]), ("#1F5F8B", "Old footer"))
		b = branding.effective(SOC, "2026-11-01")
		self.assertEqual((b["colour"], b["footer"], b["version_from"]), ("#AA0000", "Old footer", "2026-10-01"))  # a field not set falls back
		self.assertEqual(branding.effective(SOC, "2026-12-15")["footer"], "New footer")

	def test_branding_and_signatory_rows_are_checked(self):
		for rows in ([{"effective_from": "2026-10-01", "brand_colour": "#AA0000"}, {"effective_from": "2026-10-01", "footer_text": "x"}],
		             [{"effective_from": "2026-10-01"}]):
			s = self.society()
			s.set("branding_versions", rows)
			self.assertRaises(frappe.ValidationError, s.save)
		for rows in ([{"signatory_name": "A", "designation": "GM", "valid_from": "2026-01-01"}],  # signs nothing
		             [{"signatory_name": "A", "designation": "GM", "for_bill": 1, "valid_from": "2026-05-01", "valid_to": "2026-04-01"}],
		             [{"signatory_name": "A", "designation": "GM", "for_bill": 1, "valid_from": "2026-01-01"},
		              {"signatory_name": "B", "designation": "BM", "for_bill": 1, "valid_from": "2026-06-01"}]):  # both sign bills from June
			s = self.society()
			s.set("signatories", rows)
			self.assertRaises(frappe.ValidationError, s.save)
		s = self.society()
		s.set("signatories", [{"signatory_name": "A", "designation": "GM", "for_bill": 1, "valid_from": "2026-01-01", "valid_to": "2026-05-31"},
		                      {"signatory_name": "B", "designation": "BM", "for_bill": 1, "for_receipt": 1, "valid_from": "2026-06-01"}])
		s.save()
		self.assertEqual(branding.signatory(SOC, "Bill", "2026-03-01")["name"], "A")
		self.assertEqual(branding.signatory(SOC, "Bill", "2026-07-01")["name"], "B")
		self.assertIsNone(branding.signatory(SOC, "Notice", "2026-07-01"))

	# ---- printing (CLT-07, 09)
	def test_a_bill_prints_its_signatory_and_branding_and_keeps_them(self):
		s = self.society()
		s.set("signatories", [{"signatory_name": "Asad Khan", "designation": "Billing Manager", "for_bill": 1, "valid_from": "2026-01-01"}])
		s.set("branding_versions", [{"effective_from": "2026-10-15", "brand_colour": "#AA0000", "footer_text": "Serving residents since 1999"}])
		s.save()
		bill = self.october_bill()
		html = bill_print.bill_html(bill.name)
		for text in ("Asad Khan", "Billing Manager", "#AA0000", "Serving residents since 1999"):
			self.assertIn(text, html, text)
		s.reload()
		s.set("signatories", [{"signatory_name": "Someone Else", "designation": "GM", "for_bill": 1, "valid_from": "2026-01-01"}])
		s.set("branding_versions", [{"effective_from": "2026-10-15", "brand_colour": "#00AA00"}])
		s.save()
		self.assertEqual(bill_print.bill_html(bill.name), html)  # the issued bill reprints as issued (FS-RUN-09)

	def test_receipts_print_their_signatory_or_say_none_is_needed(self):
		self.september_posted_with_arrears()
		r = frappe.get_doc("Payment Receipt", {"connection": self.conn, "docstatus": 1})
		self.assertIn("no signature needed", account_print.receipt_html(r.name))
		s = self.society()
		s.set("signatories", [{"signatory_name": "Sara Malik", "designation": "Accounts Officer", "for_receipt": 1, "valid_from": "2026-01-01"}])
		s.save()
		html = account_print.receipt_html(r.name)
		self.assertIn("Sara Malik", html)
		self.assertNotIn("no signature needed", html)

	# ---- onboarding (CLT-10)
	def test_the_onboarding_checklist(self):
		frappe.set_user(BILLING_MANAGER)
		name = onboarding.open_for(SOC)
		frappe.set_user("Administrator")
		self.assertEqual(onboarding.open_for(SOC), name)  # one per society
		from utility_billing.utility_billing.testing import CASHIER

		frappe.set_user(CASHIER)
		self.assertRaises(frappe.PermissionError, onboarding.open_for, SOC)
		self.assertRaises(frappe.PermissionError, onboarding.checklist, SOC)
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Society Onboarding", name)
		self.assertEqual(len(doc.tasks), len(onboarding.DEFAULT_TASKS))
		checks = {c["check"]: c for c in onboarding.checklist(SOC)["checks"]}
		self.assertTrue(checks["Tariffs approved"]["done"])
		self.assertTrue(checks["Consumers and connections entered"]["done"])
		self.assertFalse(checks["Signatory for bills"]["done"])
		self.assertFalse(checks["Opening balances posted"]["done"])
		before = doc.percent_complete
		doc.tasks[0].done = 1
		frappe.set_user(BILLING_MANAGER)
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual((doc.tasks[0].done_by, str(doc.tasks[0].done_on)), (BILLING_MANAGER, nowdate()))
		self.assertGreater(doc.percent_complete, before)
		doc.tasks[0].done = 0
		doc.save()
		self.assertIsNone(doc.tasks[0].done_by)
