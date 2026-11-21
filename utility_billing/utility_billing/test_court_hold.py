# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Court holds after go-live (increment 36). User's decisions, 2026-11-20: "on hold will be a separate transaction just like the
installments or bill correction or due date extension. it would also generate a revised bill", and the held amount "is part of total
receivables but it is not part of payable amount unless its withdrawn".

House N 2308 (spec test case 1): the October bill is 22,260 current + 1,104 arrears = 23,364, issued 01-11-2026, due 15-11-2026;
the surcharge is 8% of the current bill (1,781)."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, bill_print, corrections, gl, held, mis
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, GENERAL_MANAGER, act

REC, HELD = "CH-1200", "CH-1250"


@contextmanager
def today(date):
	with patch.object(corrections, "nowdate", return_value=date), patch.object(account, "nowdate", return_value=date), \
			patch.object(held, "nowdate", return_value=date):
		yield


class IntegrationTestCourtHold(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		for code, name in ((REC, "Customer receivables"), (HELD, "Held receivables")):
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": "Receivable"}).insert()
		frappe.db.set_value("Society", SOC, {"surcharge_rule": "Nothing paid by the due date", "grace_days": 0,
		                                     "receivables_account": REC, "held_receivables_account": HELD})
		frappe.clear_document_cache("Society", SOC)
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.october = self.bill(self.post(self.make_run("2026-10-01", "2026-11-01")))
		self.assertEqual((self.october.current_amount, self.october.arrears, self.october.payable_within_due), (22_260, 1_104, 23_364))

	def hold(self, amount, maker=BILLING_OFFICER, **kw):
		frappe.set_user(maker)
		doc = frappe.get_doc({"doctype": "Court Hold", "connection": self.conn, "amount": amount, "court_order": "WP 4471/2025 (LHC)",
		                      "court_order_date": "2026-11-05", "remarks": "The court set the disputed detection charges aside", **kw}).insert()
		frappe.set_user("Administrator")
		return doc

	def approved(self, doc, maker=BILLING_OFFICER, approver=BILLING_MANAGER):
		act(doc, "Submit for Approval", maker)
		act(doc, "Approve", approver)
		doc.reload()
		return doc

	def test_a_hold_on_the_arrears_reissues_the_bill_without_it(self):
		with today("2026-11-10"):
			doc = self.hold(1_000)
			self.assertEqual((doc.owed_now, doc.surcharge_reversed, doc.held_from_arrears, doc.held_from_current, doc.payable_after),
			                 (23_364, 0, 1_000, 0, 22_364))
			self.assertEqual((doc.bill, str(doc.new_due_date), doc.consumer_name, doc.society), (self.october.name, "2026-11-24", doc.consumer_name, SOC))
			self.approved(doc)
			info = account.enquiry(self.conn)
		# out of what is owed, into the held amounts: part of the receivable, not payable
		e = frappe.get_doc("Customer Ledger Entry", doc.ledger_entry)
		self.assertEqual((e.entry_type, e.amount, e.d_arrears, e.voucher_type, e.voucher_no), ("Held by court order", -1_000, -1_000, "Court Hold", doc.name))
		self.assertEqual((account.balance(self.conn), held.held_of(self.conn)), (22_364, 1_000))
		self.assertEqual(gl.lines_for(e.name), [(HELD, 1_000, 0), (REC, 0, 1_000)])
		ha = frappe.get_doc("Held Amount", doc.held_amount)
		self.assertEqual((ha.status, ha.amount, ha.court_order, ha.court_hold, ha.hold_entry, str(ha.held_on)),
		                 ("Held", 1_000, "WP 4471/2025 (LHC)", doc.name, e.name, "2026-11-10"))
		# the revised bill: same charges, the held amount taken off, a new due date; the earlier version stays, Superseded
		new, old = frappe.get_doc("Utility Bill", doc.revised_bill), frappe.get_doc("Utility Bill", self.october.name)
		self.assertEqual((new.revises, new.revision_no, new.court_hold, new.connection, str(new.issue_date), str(new.due_date)),
		                 (old.name, 2, doc.name, self.conn, "2026-11-10", "2026-11-24"))
		self.assertEqual((new.current_amount, new.arrears, new.held_deducted, new.other_since_issue, new.payable_within_due,
		                  new.surcharge_amount, new.payable_after_due, new.court_held, new.ledger_entry),
		                 (22_260, 1_104, 1_000, 0, 22_364, 1_781, 24_145, 1_000, old.ledger_entry))
		self.assertEqual((old.status, old.docstatus, old.superseded_by), ("Superseded", 2, new.name))
		self.assertEqual((info["bill"], info["payable_within_due"]), (new.name, 22_364))  # payment channels ask for the revised amount
		html = bill_print.bill_html(new.name)
		for text in ("Revised, version 2", "Less: held by court order", "Held by court order, not payable: Rs 1,000", "22,364"):
			self.assertIn(text, html)

	def test_a_part_paid_bill_past_its_due_date_is_still_revised(self):
		with today("2026-11-20"):
			account.apply_surcharges(as_of="2026-11-20", society=SOC)
			self.october.reload()
			surcharge = frappe.get_doc("Customer Ledger Entry", self.october.surcharge_entry)
			self.assertEqual(surcharge.amount, 1_781)
			self.pay(5_000, "2026-11-20")  # owes 23,364 + 1,781 - 5,000 = 20,145
			doc = self.hold(3_000)
			# the latest bill's surcharge is reversed (the revised bill has its own); 1,104 from the arrears, 1,896 from the current bill
			self.assertEqual((doc.owed_now, doc.surcharge_reversed, doc.held_from_arrears, doc.held_from_current, doc.payable_after),
			                 (20_145, 1_781, 1_104, 1_896, 15_364))
			self.approved(doc, maker=BILLING_MANAGER, approver=GENERAL_MANAGER)
		self.assertTrue(frappe.db.get_value("Customer Ledger Entry", surcharge.name, "reversed_by"))
		new = frappe.get_doc("Utility Bill", doc.revised_bill)
		# the 5,000 paid shows as an entry since the earlier version; the surcharge is 8% of the current bill still payable (15,364)
		self.assertEqual((new.held_deducted, new.other_since_issue, new.payable_within_due, new.surcharge_amount, str(new.due_date),
		                  new.surcharge_status, new.surcharge_entry), (3_000, -5_000, 15_364, 1_229, "2026-12-04", "Not due yet", None))
		self.assertEqual(account.balance(self.conn), 15_364)
		# not surcharged before the new due date
		self.assertEqual(account.apply_surcharges(as_of="2026-12-04", society=SOC), 0)

	def test_released_it_comes_back_into_the_arrears(self):
		with today("2026-11-10"):
			doc = self.approved(self.hold(1_000))
			frappe.set_user(BILLING_MANAGER)
			ha = frappe.get_doc("Held Amount", doc.held_amount)
			self.assertEqual(ha.release("The court dismissed the petition"), "Released")
		frappe.set_user("Administrator")
		self.assertEqual((account.balance(self.conn), held.held_of(self.conn)), (23_364, 0))

	def test_the_next_bill_shows_the_held_amount_for_information_only(self):
		with today("2026-11-10"):
			self.approved(self.hold(1_000))
		self.pay(22_364, "2026-11-12")
		self.month_reading("2026-11-01", 4100)
		self.fpa("2026-11-01", 1.30)
		nov = self.bill(self.post(self.make_run("2026-11-01", "2026-12-01")))
		self.assertEqual((nov.court_held, nov.arrears, nov.payable_within_due), (1_000, 0, nov.current_amount))
		self.assertIn("Held by court order, not payable: Rs 1,000", bill_print.bill_html(nov.name))

	def test_the_receivables_reports_add_the_held_amount(self):
		with today("2026-11-10"):
			self.approved(self.hold(1_000))
		total = mis.arrears_ageing(frappe._dict(society=SOC, as_of="2026-11-10"))["data"][-1]
		self.assertEqual((total["held"], total["receivable"]), (1_000, round(total["total"] + 1_000, 2)))
		self.assertEqual(round(total["total"], 2), 22_364)
		dash = mis.dashboard(frappe._dict(society=SOC, month="2026-11-10"))
		values = {s["label"]: s["value"] for s in dash["summary"]}
		self.assertEqual(values["Held by court order (not payable)"], 1_000)
		self.assertEqual(values["Total receivable (owed + held)"], round(values["Total owed by consumers"] + 1_000, 2))

	def test_the_defaulters_report_shows_the_held_amount_beside_what_is_owed(self):
		"""Increment 39 (user, 2026-11-21): the held amount is shown, but only what is payable decides who is a defaulter."""
		with today("2026-11-10"):
			self.approved(self.hold(1_000))  # the revised bill is due 24-11-2026
		f = frappe._dict(society=SOC, as_of="2026-12-10", min_amount=1, min_days=1)
		row = [r for r in mis.defaulters(f)["data"] if r.get("connection") == self.conn][0]
		self.assertEqual((row["owed"], row["held"], row["receivable"]), (22_364, 1_000, 23_364))
		total = mis.defaulters(f)["data"][-1]
		self.assertEqual((total["held"], total["receivable"]), (1_000, round(total["owed"] + 1_000, 2)))
		self.assertFalse([r for r in mis.defaulters(frappe._dict(f, min_amount=22_365))["data"] if r.get("connection") == self.conn])  # held isn't counted

	def test_what_is_refused(self):
		with today("2026-11-10"):
			self.assertRaises(frappe.ValidationError, self.hold, 30_000)  # more than is owed
			self.assertRaises(frappe.ValidationError, self.hold, 1_000, court_order=" ")
			doc = self.hold(1_000)
			self.assertRaises(frappe.ValidationError, self.hold, 500)  # one open hold per account
			act(doc, "Submit for Approval", BILLING_OFFICER)
			self.assertRaises(Exception, act, doc, "Approve", BILLING_OFFICER)  # a Billing Officer doesn't approve
			doc.reload()
			self.assertEqual(doc.workflow_state, "Pending Approval")
			act(doc, "Approve", BILLING_MANAGER)
			doc.reload()
		frappe.set_user("Administrator")
		self.assertRaises(frappe.ValidationError, doc.cancel)  # released or written off on the Held Amount, never undone

	def test_the_held_receivables_account_is_needed(self):
		frappe.db.set_value("Society", SOC, "held_receivables_account", None)
		frappe.clear_document_cache("Society", SOC)
		with today("2026-11-10"):
			doc = self.hold(1_000)
			act(doc, "Submit for Approval", BILLING_OFFICER)
			self.assertRaises(Exception, act, doc, "Approve", BILLING_MANAGER)
		self.assertEqual(account.balance(self.conn), 23_364)
