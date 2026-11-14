# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for increment 7: customer account, payments, cashier day, surcharge, fees, reason
codes, credit/debit notes, statement (FS-REC, FS-PAY, FS-CFG-17, 24, 26), including spec SC-19."""

from unittest.mock import patch

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, account_print, bill_print
from utility_billing.utility_billing.doctype.cashier_day.cashier_day import approve_day, close_day
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	ACCOUNTS_OFFICER,
	BILLING_MANAGER,
	BM_AND_GM,
	BILLING_OFFICER,
	CASHIER,
	CASHIER_2,
	GENERAL_MANAGER,
	act,
)


class IntegrationTestAccounts(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		if not frappe.db.exists("Cash Counter", "TB1"):
			frappe.get_doc({"doctype": "Cash Counter", "counter_code": "TB1", "counter_name": "Test counter",
			                "society": SOC}).insert()
		# The sample bill (test case 1) shows no surcharge on a partly paid September, so the test society
		# uses "Nothing paid by the due date"; test_strict_rule covers the default.
		frappe.db.set_value("Society", SOC, {"allow_part_payment": 1, "allow_overpayment": 1, "grace_days": 0,
		                                     "payment_posting_order": "Current bill, Arrears, Surcharge",
		                                     "surcharge_rule": "Nothing paid by the due date", "duplicate_bill_fee": None})

	def on(self, date):
		"""Pretend today is `date` for receipt checks (payment dates can't be in the future)."""
		return patch("utility_billing.utility_billing.doctype.payment_receipt.payment_receipt.nowdate", return_value=date)

	# ----- helpers -----
	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		return run, self.bill(run)

	def bal(self):
		return {k: float(v) for k, v in account.balances(self.conn).items()}

	def open_day(self, user=CASHIER, counter="TB1", opening=1000):
		frappe.set_user(user)
		try:
			return frappe.get_doc({"doctype": "Cashier Day", "cash_counter": counter, "opening_float": opening}).insert()
		finally:
			frappe.set_user("Administrator")

	def counter_receipt(self, amount, user=CASHIER, mode="Cash", **kw):
		frappe.set_user(user)
		try:
			r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Counter",
			                    "amount": amount, "mode": mode, **kw}).insert()
			r.submit()
			return r
		finally:
			frappe.set_user("Administrator")

	def reverse(self, doctype, name, user=BILLING_MANAGER, reason="CHQ-BOUNCE", remarks="Cheque returned unpaid"):
		frappe.set_user(user)
		try:
			return account.reverse_document(doctype, name, reason, remarks)
		finally:
			frappe.set_user("Administrator")

	# ----- SC-19 and the surcharge -----
	def test_sc19_payment_posting_order(self):
		run, bill = self.october_posted()
		self.assertEqual(self.bal(), {"current": 22260, "arrears": 1104, "surcharge": 0, "fees": 0, "credit": 0})
		self.assertEqual(account.apply_surcharges(as_of="2026-11-16", society=SOC), 1)  # day after the due date
		bill.reload()
		self.assertEqual(bill.surcharge_status, "Posted")
		self.assertEqual(self.bal()["surcharge"], 1781)
		with self.on("2026-11-20"):
			r = self.pay(23000, "2026-11-20")
		self.assertEqual((r.alloc_current, r.alloc_arrears, r.alloc_surcharge, r.alloc_credit), (22260, 740, 0, 0))
		self.assertEqual(self.bal(), {"current": 0, "arrears": 364, "surcharge": 1781, "fees": 0, "credit": 0})  # SC-19
		self.assertEqual(r.balance_after, 2145)

	def test_no_surcharge_when_paid_by_due_date(self):
		run, bill = self.october_posted()
		self.pay(23364, "2026-11-14")
		self.assertEqual(account.apply_surcharges(as_of="2026-11-16", society=SOC), 0)
		bill.reload()
		self.assertEqual(bill.surcharge_status, "Not charged (paid in time)")
		self.assertEqual(account.balance(self.conn), 0)

	def test_strict_rule_surcharges_a_partly_paid_bill(self):
		"""Default rule: anything unpaid after the due date is surcharged (September: Rs 1,104 unpaid)."""
		frappe.db.set_value("Society", SOC, "surcharge_rule", "Not paid in full by the due date")
		self.september_posted_with_arrears()
		sep = frappe.get_doc("Utility Bill", {"connection": self.conn, "billing_month": "2026-09-01"})
		self.assertEqual(account.apply_surcharges(as_of="2026-10-16", society=SOC), 1)
		self.assertEqual(self.bal()["surcharge"], sep.surcharge_amount)
		# Under "Nothing paid by the due date" the same September bill would not be surcharged.
		frappe.db.set_value("Society", SOC, "surcharge_rule", "Nothing paid by the due date")
		sep.reload()
		self.assertTrue(account.paid_in_time(sep, account.surcharge_deadline(sep)))

	def test_surcharge_waits_for_grace_days(self):
		run, bill = self.october_posted()
		frappe.db.set_value("Society", SOC, "grace_days", 3)
		self.assertEqual(account.apply_surcharges(as_of="2026-11-17", society=SOC), 0)
		self.assertEqual(account.apply_surcharges(as_of="2026-11-19", society=SOC), 1)

	def test_payment_dated_by_due_date_reverses_a_posted_surcharge(self):
		"""FS-PAY-08: paid at the bank on the due date, reported after the surcharge was added."""
		run, bill = self.october_posted()
		account.apply_surcharges(as_of="2026-11-16", society=SOC)
		with self.on("2026-11-18"):
			self.pay(23364, "2026-11-15")
		bill.reload()
		self.assertEqual(bill.surcharge_status, "Reversed (paid in time)")
		self.assertEqual(account.balance(self.conn), 0)
		self.assertTrue(frappe.db.get_value("Customer Ledger Entry", bill.surcharge_entry, "reversed_by"))

	# ----- the ledger -----
	def test_ledger_entries_running_balance_and_immutable(self):
		run, bill = self.october_posted()
		rows = frappe.get_all("Customer Ledger Entry", filters={"connection": self.conn},
		                      fields=["seq", "entry_type", "amount", "balance"], order_by="seq")
		self.assertEqual([(r.seq, r.entry_type) for r in rows], [(1, "Bill"), (2, "Payment"), (3, "Bill")])
		self.assertEqual([r.balance for r in rows], [59470, 1104, 23364])
		entry = frappe.get_doc("Customer Ledger Entry", {"connection": self.conn, "seq": 3})
		entry.amount = 1
		with self.assertRaises(frappe.ValidationError):
			entry.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Customer Ledger Entry", entry.name, ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Customer Ledger Entry", "connection": self.conn, "amount": 5}).insert(ignore_permissions=True)
		self.assertEqual(bill.arrears, 1104)  # the bill's arrears came from the account

	def test_overpayment_is_credit_used_by_the_next_bill(self):
		self.month_reading("2026-08-01", 2830)
		self.fpa("2026-08-01", 0)
		aug = self.bill(self.post(self.make_run("2026-08-01", "2026-09-01")))
		r = self.pay(aug.payable_within_due + 5000, "2026-09-05")
		self.assertEqual((r.alloc_credit, r.balance_after), (5000, -5000))
		self.month_reading("2026-09-01", 3641)
		self.fpa("2026-09-01", 0)
		sep = self.bill(self.make_run("2026-09-01", "2026-10-01"))
		self.assertEqual(sep.arrears, -5000)
		self.assertEqual(sep.payable_within_due, sep.current_amount - 5000)
		self.post(frappe.get_doc("Bill Run", sep.bill_run))
		self.assertEqual(self.bal()["credit"], 0)
		self.assertEqual(self.bal()["current"], sep.current_amount - 5000)

	def test_part_and_overpayment_settings(self):
		run, bill = self.october_posted()
		frappe.db.set_value("Society", SOC, {"allow_part_payment": 0, "allow_overpayment": 0})
		frappe.clear_document_cache("Society", SOC)
		for amount in (100, 99999):
			with self.assertRaises(frappe.ValidationError):
				self.pay(amount, "2026-11-10")
		self.pay(23364, "2026-11-10")

	def test_channel_transaction_posts_once(self):
		run, bill = self.october_posted()
		self.pay(100, "2026-11-10", txn="1LINK-778899")
		with self.assertRaises(frappe.ValidationError):
			self.pay(100, "2026-11-11", txn="1LINK-778899")
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):  # bank/channel payments are for Accounts Officers
			frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Bank / Channel",
			                "payment_date": "2026-11-10", "amount": 10, "mode": "1Link", "channel_txn_id": "X1"}).insert()
		frappe.set_user(ACCOUNTS_OFFICER)
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Bank / Channel",
		                    "payment_date": "2026-11-10", "amount": 10, "mode": "1Link", "channel_txn_id": "X2"}).insert()
		r.submit()
		self.assertTrue(r.name.startswith("BNK-"))

	# ----- cashier day and counter receipts -----
	def test_counter_day_receipts_close_and_approve(self):
		run, bill = self.october_posted()
		with self.assertRaises(frappe.ValidationError):  # no open day yet
			self.counter_receipt(100)
		day = self.open_day()
		with self.assertRaises(frappe.ValidationError):  # one open day per counter
			self.open_day(user=CASHIER_2)
		r1 = self.counter_receipt(20000)
		r2 = self.counter_receipt(3000, mode="Cheque", instrument_no="000123", bank_name="MBL")
		self.assertEqual((r1.name, r2.name), ("TB1-00001", "TB1-00002"))  # FS-PAY-13: in sequence per counter
		self.assertEqual((r1.cashier_day, r1.payment_date.__str__()), (day.name, frappe.utils.nowdate()))
		with self.assertRaises(frappe.ValidationError):  # cheque needs its number
			self.counter_receipt(10, mode="Cheque")
		day.reload()
		self.assertEqual((day.receipt_count, day.cash_total, day.cheque_total, day.expected_cash), (2, 20000, 3000, 21000))

		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.ValidationError):  # count differs, no explanation
			close_day(day.name, 20900)
		close_day(day.name, 20900, "DS-55", "Rs 100 short; cashier to make good")
		day.reload()
		self.assertEqual((day.status, day.difference, day.deposit_slip_no), ("Closed", -100, "DS-55"))
		with self.assertRaises(frappe.ValidationError):  # nothing after closing
			self.counter_receipt(10)
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):  # a cashier doesn't approve days
			approve_day(day.name)
		frappe.set_user(ACCOUNTS_OFFICER)
		approve_day(day.name)
		frappe.set_user("Administrator")
		day.reload()
		self.assertEqual((day.status, day.approved_by), ("Approved", ACCOUNTS_OFFICER))
		day.opening_float = 5
		with self.assertRaises(frappe.ValidationError):
			day.save()

	def test_cashier_cannot_cancel_and_manager_reverses_with_reason(self):
		run, bill = self.october_posted()
		self.open_day()
		r = self.counter_receipt(23364, mode="Cheque", instrument_no="445566")
		self.assertEqual(account.balance(self.conn), 0)
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			account.reverse_document("Payment Receipt", r.name, "CHQ-BOUNCE", "Returned")
		frappe.set_user("Administrator")
		r.reload()
		with self.assertRaises(frappe.ValidationError):  # plain cancel is refused
			r.cancel()
		with self.assertRaises(frappe.ValidationError):  # a reason of the wrong kind
			self.reverse("Payment Receipt", r.name, reason="SUR-WAIVE")
		with self.assertRaises(frappe.ValidationError):  # an explanation is required
			self.reverse("Payment Receipt", r.name, remarks=" ")
		self.reverse("Payment Receipt", r.name)
		r.reload()
		self.assertEqual((r.docstatus, r.status, r.reversal_reason, r.reversed_by), (2, "Reversed", "CHQ-BOUNCE", BILLING_MANAGER))
		self.assertEqual(account.balance(self.conn), 23364)  # what it paid is owed again
		reversal = frappe.get_doc("Customer Ledger Entry", {"reverses": r.ledger_entry})
		self.assertEqual((reversal.entry_type, reversal.amount), ("Reversal", 23364))

	# ----- fees, duplicate bill fee, notes -----
	def test_fee_schedule_and_duplicate_bill_fee(self):
		run, bill = self.october_posted()
		fee_type = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Duplicate Bill", "amount": 100}).insert()
		frappe.db.set_value("Society", SOC, "duplicate_bill_fee", fee_type.name)
		bill_print.issue_duplicate(bill.name)
		fee = frappe.get_doc("Fee Charge", {"source_name": bill.name})
		self.assertEqual((fee.docstatus, fee.amount, fee.status), (1, 100, "Posted"))
		self.assertEqual(self.bal()["fees"], 100)
		self.reverse("Fee Charge", fee.name, reason="FEE-ERROR", remarks="Consumer had not lost the bill")
		self.assertEqual(self.bal()["fees"], 0)
		frappe.db.set_value("Fee Type", fee_type.name, "enabled", 0)
		with self.assertRaises(frappe.ValidationError):  # disabled fees can't be charged
			frappe.get_doc({"doctype": "Fee Charge", "connection": self.conn, "fee_type": fee_type.name}).insert()

	def test_surcharge_waiver_credit_note_with_approval(self):
		run, bill = self.october_posted()
		account.apply_surcharges(as_of="2026-11-16", society=SOC)
		frappe.set_user(BM_AND_GM)
		note = frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": self.conn,
		                       "against_bill": bill.name, "bucket": "Surcharge", "amount": 1781, "reason_code": "SUR-WAIVE",
		                       "remarks": "Board approved waiver for October"}).insert()
		frappe.set_user("Administrator")
		self.assertTrue(note.name.startswith("CN-"))
		self.assertEqual(self.bal()["surcharge"], 1781)  # nothing posted before approval
		act(note, "Submit for Approval", BM_AND_GM)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):  # maker can't approve, even as GM
			act(note, "Approve", BM_AND_GM)
		act(note, "Approve", GENERAL_MANAGER)
		note.reload()
		self.assertEqual((note.docstatus, note.workflow_state), (1, "Approved"))
		self.assertEqual(self.bal()["surcharge"], 0)
		self.assertEqual(frappe.db.get_value("Customer Ledger Entry", note.ledger_entry, "entry_type"), "Credit Note")

	def test_credit_note_cannot_exceed_balance_and_needs_waiver_reason(self):
		run, bill = self.october_posted()
		frappe.set_user(BILLING_OFFICER)
		big = frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": self.conn,
		                      "bucket": "Arrears", "amount": 2000, "reason_code": "BILL-ERROR", "remarks": "x"}).insert()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			act(big, "Submit for Approval", BILLING_OFFICER)
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Debit Note", "connection": self.conn,
			                "bucket": "Fees", "amount": 50, "reason_code": "CHQ-BOUNCE", "remarks": "x"}).insert()
		frappe.set_user("Administrator")

	# ----- bills and runs -----
	def test_bill_history_shows_payments(self):
		run, bill = self.october_posted()
		html = bill_print.bill_html(bill.name)
		hist = bill_print.snapshot_of(bill)["history"]
		self.assertEqual((hist[0]["month"], hist[0]["paid"], hist[0]["paid_on"]), ("2026-09-01", 58366, "2026-10-10"))
		self.assertIn("58,366", html)

	def test_cancelling_a_run_is_blocked_by_later_payments(self):
		run, bill = self.october_posted()
		r = self.pay(1000, "2026-11-05")
		run.reload()
		with self.assertRaises(frappe.ValidationError):
			act(run, "Cancel", BILLING_MANAGER)
		self.reverse("Payment Receipt", r.name, reason="WRONG-ACC", remarks="Belongs to another house")
		run.reload()
		act(run, "Cancel", BILLING_MANAGER)
		bill.reload()
		self.assertEqual(bill.docstatus, 2)
		self.assertEqual(account.balance(self.conn), 1104)  # back to before the October bill

	# ----- enquiry and statement -----
	def test_enquiry_and_statement(self):
		run, bill = self.october_posted()
		e = account.enquiry(self.conn)
		self.assertEqual((e["bill"], e["payable_within_due"], e["amount_due"], e["consumer_name"]),
		                 (bill.name, 23364, 23364, "House N 2308 Owner"))
		# Without an end date the statement closes at the balance now (found in the visual check: a
		# statement ending today left out entries dated later, so its closing balance disagreed).
		self.assertEqual(account.statement(self.conn)["closing"], account.balance(self.conn))
		s = account.statement(self.conn, "2026-10-05", "2026-11-30")
		self.assertEqual((s["opening"], [r.entry_type for r in s["rows"]], s["closing"]), (59470, ["Payment", "Bill"], 23364))
		html = account_print.statement_html(self.conn, "2026-10-05", "2026-11-30")
		for text in ("Account Statement", "Opening balance", "59,470", "58,366", "23,364", "Closing balance"):
			self.assertIn(text, html)
		r = self.pay(364, "2026-11-10")
		receipt = account_print.receipt_html(r.name)
		for text in ("Payment Receipt", r.name, "364", self.conn, "Current bill"):
			self.assertIn(text, receipt)
		account_print.download_statement(self.conn, "2026-10-01", "2026-11-30")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		account_print.download_receipt(r.name)
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			account.enquiry(self.conn)
