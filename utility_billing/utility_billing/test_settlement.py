# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for settlement files, suspense payments and refunds (increment 12; FS-PAY-10, 11, 16, FS-WF-13).
House N 2308 (spec test case 1) is the account: after the October bill it owes 22,260 + 1,104 = 23,364."""

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, gl, mis, settlement
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	ACCOUNTS_OFFICER, BILLING_MANAGER, BILLING_OFFICER, CASHIER, GENERAL_MANAGER, act,
)

HEADER = "transaction_id,reference_no,amount,date\n"


class IntegrationTestSettlement(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		self.ref = frappe.db.get_value("Connection", self.conn, "reference_no")
		self.october_posted()

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def file(self, **kw):
		return frappe.get_doc({"doctype": "Settlement File", "society": SOC, "mode": "Bank Transfer", "file_date": "2026-11-13",
		                       "source_name": "MBL", **kw}).insert()

	def import_(self, content, **kw):
		f = self.file(**kw)
		settlement.import_rows(f, content)
		f.save()
		return f

	def rows(self, f):
		return {r.txn_id: r for r in f.rows}

	def recorded(self):
		"""Two recorded payments: T1 1,000 and T2 500, plus T5 300 that the file will not list."""
		self.pay(1000, "2026-11-10", txn="T1")
		self.pay(500, "2026-11-11", txn="T2")
		self.pay(300, "2026-11-12", txn="T5")

	def sample(self):
		self.recorded()
		return self.import_(HEADER + f"T1,{self.ref},1000,2026-11-10\nT2,{self.ref},600,2026-11-11\nT3,{self.ref},700,2026-11-12\n"
		                    f"T4,WRONG-REF,300,2026-11-12\nT1,{self.ref},1000,2026-11-10\n")

	# ---- matching (PAY-10)
	def test_every_row_is_matched_or_flagged(self):
		f = self.sample()
		by = {(r.txn_id, r.idx): r.result for r in f.rows}
		self.assertEqual([r.result for r in f.rows], ["Matched", "Amount differs", "Not recorded", "Not recorded", "Duplicate in file", "Missing from file"])
		self.assertEqual((f.status, f.rows_in_file, f.matched, f.amount_differs, f.not_recorded, f.missing_from_file, f.other_flags),
		                 ("Matched", 5, 1, 1, 2, 1, 1))
		rows = self.rows(f)
		self.assertEqual((rows["T2"].amount, rows["T2"].recorded_amount), (600, 500))
		self.assertEqual(rows["T5"].receipt, frappe.db.get_value("Payment Receipt", {"channel_txn_id": "T5"}))
		self.assertEqual(f.total_amount, 1000 + 600 + 700 + 300)  # the duplicate row isn't counted twice

	def test_a_reversed_receipt_is_flagged(self):
		self.recorded()
		r = frappe.db.get_value("Payment Receipt", {"channel_txn_id": "T1"})
		frappe.set_user(BILLING_MANAGER)
		account.reverse_document("Payment Receipt", r, "CHQ-BOUNCE", "Cheque returned unpaid")
		frappe.set_user("Administrator")
		f = self.import_(HEADER + f"T1,{self.ref},1000,2026-11-10\n")
		self.assertEqual(f.rows[0].result, "Recorded but reversed")

	def test_receipts_of_another_channel_or_society_are_not_matched(self):
		self.recorded()
		f = self.import_(HEADER + f"T1,{self.ref},1000,2026-11-10\n", mode="1Link")
		self.assertEqual(f.rows[0].result, "Not recorded")  # T1 was recorded as Bank Transfer

	def test_an_unreadable_file_is_refused_with_the_reason(self):
		f = self.file()
		with self.assertRaises(frappe.ValidationError) as cm:
			settlement.import_rows(f, "a,b\n1,2\n")
		self.assertIn("Missing column", str(cm.exception))
		with self.assertRaises(frappe.ValidationError):
			settlement.import_rows(f)  # nothing attached

	# ---- posting and suspense (PAY-10, 11)
	def test_unrecorded_money_is_posted_or_held_never_dropped(self):
		f = self.sample()
		out = settlement.post_unrecorded(f)
		f.save()
		self.assertEqual(out, {"posted": 1, "suspense": 1})
		rows = self.rows(f)
		self.assertEqual((rows["T3"].result, rows["T3"].action_taken), ("Matched", "Posted"))
		r = frappe.get_doc("Payment Receipt", rows["T3"].receipt)
		self.assertEqual((r.amount, r.connection, r.channel_txn_id, r.docstatus, str(r.payment_date)), (700, self.conn, "T3", 1, "2026-11-12"))
		self.assertEqual((rows["T4"].action_taken, bool(rows["T4"].suspense)), ("Suspense", True))
		s = frappe.get_doc("Suspense Payment", rows["T4"].suspense)
		self.assertEqual((s.status, s.amount, s.reference_text, s.channel_txn_id, s.settlement_file), ("Held", 300, "WRONG-REF", "T4", f.name))
		self.assertEqual((f.not_recorded, f.matched), (1, 2))  # the held one is still "not recorded" in the file's own count
		again = settlement.post_unrecorded(f)
		self.assertEqual(again, {"posted": 0, "suspense": 0})  # nothing is posted twice

	def test_only_an_accounts_officer_posts_from_a_file(self):
		f = self.sample()
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			settlement.post_unrecorded(f)

	def test_a_reference_of_another_society_goes_to_suspense(self):
		other = frappe.get_doc({"doctype": "Society", "society_name": "Other Test Society", "short_name": "TST-OTHER", "energy_charge_type": "TST Energy"}).insert()
		f = self.import_(HEADER + f"X1,{self.ref},400,2026-11-12\n", society=other.name)
		self.assertEqual(settlement.post_unrecorded(f), {"posted": 0, "suspense": 1})

	def test_a_suspense_payment_is_matched_to_an_account_and_posted_once(self):
		s = settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "S1", "no such reference")
		before = account.balance(self.conn)
		r = settlement.match_suspense(s, self.conn)
		s.save()
		s.reload()
		self.assertEqual((s.status, s.matched_connection, s.payment_receipt, s.resolved_by), ("Matched", self.conn, r.name, "Administrator"))
		self.assertEqual((r.amount, r.channel_txn_id, r.docstatus), (300, "S1", 1))
		self.assertEqual(account.balance(self.conn), before - 300)
		with self.assertRaises(frappe.ValidationError):
			settlement.match_suspense(s, self.conn)

	def test_matching_updates_the_settlement_row_that_pointed_at_it(self):
		f = self.sample()
		settlement.post_unrecorded(f)
		f.save()
		s = frappe.get_doc("Suspense Payment", self.rows(f)["T4"].suspense)
		r = settlement.match_suspense(s, self.conn)
		s.save()
		row = frappe.db.get_value("Settlement Row", {"parent": f.name, "txn_id": "T4"}, ["result", "receipt"], as_dict=True)
		self.assertEqual((row.result, row.receipt), ("Matched", r.name))
		counts = frappe.db.get_value("Settlement File", f.name, ["matched", "not_recorded"], as_dict=True)
		self.assertEqual((counts.matched, counts.not_recorded), (3, 0))  # the file's own counts follow its rows

	def test_a_suspense_payment_can_be_returned_with_a_remark_and_then_is_locked(self):
		s = settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "S2", "unknown")
		with self.assertRaises(frappe.ValidationError):
			settlement.return_suspense(s, " ")
		settlement.return_suspense(s, "Refunded by bank transfer to the payer")
		s.save()
		s.reload()
		self.assertEqual(s.status, "Returned to Payer")
		with self.assertRaises(frappe.ValidationError):
			settlement.match_suspense(s, self.conn)
		s.amount = 1
		with self.assertRaises(frappe.ValidationError):
			s.save()

	def test_a_transaction_posts_only_once_even_through_suspense(self):
		settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "S3", "x")
		with self.assertRaises(frappe.ValidationError):
			settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "S3", "again")
		self.pay(100, "2026-11-12", txn="S4")
		with self.assertRaises(frappe.ValidationError):
			settlement.new_suspense(SOC, 100, "2026-11-12", "Bank Transfer", "S4", "already a payment")

	def test_suspense_rights(self):
		s = settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "S5", "x")
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			settlement.match_suspense(s, self.conn)
		with self.assertRaises(frappe.PermissionError):
			settlement.return_suspense(s, "n")
		frappe.set_user(BILLING_MANAGER)  # may handle, but posting needs an Accounts Officer
		with self.assertRaises(frappe.PermissionError):
			settlement.match_suspense(s, self.conn)
		settlement.return_suspense(s, "Returned")

	# ---- closing a file
	def test_a_file_closes_only_when_every_flag_has_an_action_or_a_remark(self):
		f = self.sample()
		with self.assertRaises(frappe.ValidationError) as cm:
			settlement.close_file(f)
		self.assertIn("flagged", str(cm.exception))
		settlement.post_unrecorded(f)
		for r in f.rows:
			if r.result in ("Amount differs", "Duplicate in file", "Missing from file"):
				r.remarks = "Bank notified, correction expected tomorrow"
		for r in f.rows:
			if r.result == "Not recorded":  # the held one: its action is Suspense already
				self.assertEqual(r.action_taken, "Suspense")
		settlement.close_file(f)
		f.flags.closing = True
		f.save()
		f = frappe.get_doc("Settlement File", f.name)  # a fresh copy: flags survive a reload
		self.assertEqual((f.status, f.closed_by), ("Closed", "Administrator"))
		f.source_name = "other"
		with self.assertRaises(frappe.ValidationError):
			f.save()
		with self.assertRaises(frappe.ValidationError):
			settlement.import_rows(f, HEADER + f"T9,{self.ref},1,2026-11-10\n")

	def test_a_file_must_be_imported_before_it_is_closed(self):
		with self.assertRaises(frappe.ValidationError):
			settlement.close_file(self.file())

	def test_the_exceptions_report_lists_open_rows_only_until_they_are_dealt_with(self):
		f = self.sample()
		cols, data, *_ = mis.run("settlement_exceptions", {"society": SOC, "from_date": "2026-11-01", "to_date": "2026-11-30", "open_only": 1})
		self.assertEqual(sorted(r["result"] for r in data), ["Amount differs", "Duplicate in file", "Missing from file", "Not recorded", "Not recorded"])
		diff = [r for r in data if r["result"] == "Amount differs"][0]
		self.assertEqual((diff["amount"], diff["recorded_amount"], diff["difference"]), (600, 500, 100))
		settlement.post_unrecorded(f)
		f.save()
		cols, data, *_ = mis.run("settlement_exceptions", {"society": SOC, "from_date": "2026-11-01", "to_date": "2026-11-30", "open_only": 1})
		self.assertEqual(sorted(r["result"] for r in data), ["Amount differs", "Duplicate in file", "Missing from file"])
		cols, data, *_ = mis.run("settlement_exceptions", {"society": SOC, "from_date": "2026-11-01", "to_date": "2026-11-30", "open_only": 0})
		self.assertEqual(len(data), 4)  # the row held in suspense stays on record; the posted one is now matched

	def test_channel_fees_are_recorded_per_row_and_totalled(self):
		f = self.import_("transaction_id,reference_no,amount,date,fee\nA1,%s,1000,2026-11-10,12.5\nA2,%s,500,2026-11-10,6\n" % (self.ref, self.ref))
		self.assertEqual(f.total_fees, 18.5)

	def test_the_suspense_report(self):
		settlement.new_suspense(SOC, 300, "2026-11-01", "Bank Transfer", "R1", "x")
		settlement.new_suspense(SOC, 200, "2026-11-10", "1Link", "R2", "y")
		cols, data, *_ = mis.run("suspense_payments", {"society": SOC, "as_of": "2026-11-14"})
		self.assertEqual([(r["channel_txn_id"], r["age"]) for r in data[:2]], [("R1", 13), ("R2", 4)])
		self.assertEqual(data[-1]["amount"], 500)

	# ---- refunds (PAY-16, WF-13)
	def credit(self, amount=30_000):
		"""Overpay the account: 30,000 paid against 23,364 owed leaves 6,636 of credit."""
		self.pay(amount, "2026-11-12", txn=frappe.generate_hash(length=8))

	def refund(self, amount=6_000, **kw):
		return frappe.get_doc({"doctype": "Refund", "connection": self.conn, "amount": amount, "reason_code": "REFUND-EXCESS", "refund_mode": "Bank Transfer",
		                       "payee_name": "House N 2308 Owner", "instrument_no": "PK00 MBL 1234", **kw}).insert()

	def test_a_refund_is_approved_by_another_person_and_reduces_the_credit(self):
		self.credit()
		self.assertEqual(account.balances(self.conn)["credit"], 6636)
		rf = self.refund(6_000)
		self.assertEqual(rf.workflow_state, "Draft")
		act(rf, "Submit for Approval", ACCOUNTS_OFFICER)
		rf.reload()
		self.assertEqual(rf.workflow_state, "Pending Approval")
		with self.assertRaises(Exception):
			act(rf, "Approve", ACCOUNTS_OFFICER)  # not an approver
		act(rf, "Approve", GENERAL_MANAGER)
		rf.reload()
		self.assertEqual((rf.workflow_state, rf.docstatus, rf.approved_by), ("Approved", 1, GENERAL_MANAGER))
		e = frappe.get_doc("Customer Ledger Entry", rf.ledger_entry)
		self.assertEqual((e.entry_type, e.amount, e.d_credit, e.credit_balance, e.voucher_no), ("Refund", 6000, -6000, 636, rf.name))
		self.assertEqual(account.balances(self.conn)["credit"], 636)

	def test_a_refund_can_not_exceed_the_credit(self):
		self.credit()
		rf = self.refund(7_000)
		with self.assertRaises(Exception) as cm:
			act(rf, "Submit for Approval", BILLING_OFFICER)
		self.assertIn("credit", str(cm.exception))
		with self.assertRaises(frappe.ValidationError):
			self.refund(0)
		own = self.refund(100)
		own.reason_code = "NON-PAY"
		with self.assertRaises(frappe.ValidationError):
			own.save()  # the reason must be a refund reason

	def test_the_credit_can_be_spent_before_the_refund_is_approved(self):
		self.credit()
		first, second = self.refund(6_000), self.refund(6_000)  # each fits the 6,636 of credit when it is submitted
		act(first, "Submit for Approval", BILLING_OFFICER)
		act(second, "Submit for Approval", BILLING_OFFICER)
		act(first, "Approve", GENERAL_MANAGER)
		with self.assertRaises(Exception) as cm:
			act(second, "Approve", GENERAL_MANAGER)  # only 636 is left: approving re-checks the credit
		self.assertIn("credit", str(cm.exception))
		self.assertEqual(account.balances(self.conn)["credit"], 636)

	def test_a_refund_by_cheque_needs_the_cheque_number_and_a_payee(self):
		self.credit()
		with self.assertRaises(frappe.ValidationError):
			self.refund(refund_mode="Cheque", instrument_no="")
		with self.assertRaises(frappe.ValidationError):
			self.refund(payee_name=" ")
		self.refund(refund_mode="Cheque", instrument_no="CHQ-0001")

	def test_a_cancelled_refund_gives_the_credit_back(self):
		self.credit()
		rf = self.refund(6_000)
		act(rf, "Submit for Approval", BILLING_OFFICER)
		act(rf, "Approve", GENERAL_MANAGER)
		act(rf, "Cancel", GENERAL_MANAGER)
		self.assertEqual(account.balances(self.conn)["credit"], 6636)
		rf.reload()
		self.assertEqual(rf.docstatus, 2)

	def test_a_refund_is_journaled_as_money_out(self):
		for code, name, kind in (("T-1200", "Customer receivables", "Receivable"), ("T-1020", "MBL current account", "Bank")):
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		soc = frappe.get_doc("Society", SOC)
		soc.receivables_account = "T-1200"
		soc.set("payment_accounts", [{"mode": "Bank Transfer", "account": "T-1020"}])
		soc.save()
		self.credit()
		rf = self.refund(6_000)
		act(rf, "Submit for Approval", BILLING_OFFICER)
		act(rf, "Approve", GENERAL_MANAGER)
		rf.reload()
		self.assertEqual(sorted(gl.lines_for(rf.ledger_entry)), [("T-1020", 0, 6000), ("T-1200", 6000, 0)])
		soc.set("payment_accounts", [])
		soc.save()
		with self.assertRaises(gl.MissingAccount):
			gl.lines_for(rf.ledger_entry)  # no bank account mapped for the mode: the journal waits, the refund is not blocked

	def test_the_refund_register_and_pending_approvals(self):
		self.credit()
		rf = self.refund(6_000)
		act(rf, "Submit for Approval", BILLING_OFFICER)
		cols, data, *_ = mis.run("pending_approvals", {"society": SOC})
		self.assertIn(("Refund", rf.name, "General Manager"), [(r["doctype"], r["document"], r["waiting_for"]) for r in data])
		act(rf, "Approve", GENERAL_MANAGER)
		cols, data, *_ = mis.run("refunds_register", {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31"})
		self.assertEqual((data[0]["name"], data[0]["amount"], data[0]["workflow_state"], data[0]["approved_by"]), (rf.name, 6000, "Approved", GENERAL_MANAGER))
		self.assertEqual(data[-1]["amount"], 6000)

	def test_the_dashboard_points_to_suspense_and_open_files(self):
		from unittest.mock import patch

		from utility_billing.utility_billing import dashboard

		settlement.new_suspense(SOC, 300, "2026-11-12", "Bank Transfer", "D1", "x")
		self.sample()
		with patch.object(dashboard, "nowdate", return_value="2026-11-14"):
			att = dashboard.get_dashboard(society=SOC, month="2026-11-01")["attention"]
		titles = [i["title"] for i in att]
		self.assertTrue(any("held in suspense" in t for t in titles), titles)
		self.assertTrue(any("settlement files are waiting" in t for t in titles), titles)
		item = next(i for i in att if "held in suspense" in i["title"])
		self.assertEqual((item["route"], item["options"]["status"]), (["List", "Suspense Payment"], "Held"))

	def test_new_things_are_in_the_menu_and_reports_are_registered(self):
		links = {i.link_to for i in frappe.get_doc("Sidebar", "Utility Management System").items}
		self.assertTrue({"Refund", "Settlement File", "Suspense Payment", "Message Log", "Broadcast", "Notification Template", "Messaging Provider",
		                 "Message Cost by Channel", "Settlement Exceptions", "Suspense Payments", "Refunds Register"} <= links)
