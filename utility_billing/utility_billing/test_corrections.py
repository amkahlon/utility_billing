# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for corrections (increment 13; FS-BIL-10, 11, FS-WF-01, 03, 06, 07, 10, 11, 12, FS-RUN-06).
House N 2308 (spec test case 1): the October bill is 22,260 for 264 units (3,905 - 3,641) at 70 + FPA 1,080 + service 1,700 +
aquifer 1,000, issued 01-11-2026 and due 15-11-2026, with 1,104 of arrears from September: payable 23,364. Time is fixed with
`today()` so the due date window doesn't depend on the day the tests run."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, approval, bill_print, corrections, mis, recovery
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import (
	BILLING_MANAGER, BILLING_MANAGER_2, BILLING_OFFICER, GENERAL_MANAGER, act, approve,
)


@contextmanager
def today(date):
	with patch.object(corrections, "nowdate", return_value=date), patch.object(account, "nowdate", return_value=date):
		yield


class IntegrationTestCorrections(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		frappe.db.set_value("Society", SOC, "surcharge_rule", "Nothing paid by the due date")  # September was paid by its due date
		frappe.clear_document_cache("Society", SOC)
		self.run = self.october_posted()
		self.bill_doc = self.bill(self.run)

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def balance(self):
		return account.balance(self.conn)

	# ---- helpers
	def revision(self, bill=None, kind="Corrected reading", **kw):
		values = {"doctype": "Bill Revision", "original_bill": (bill or self.bill_doc).name, "kind": kind, "reason_code": "READ-WRONG",
		          "remarks": "The reader mistyped the present reading", **kw}
		if kind == "Corrected reading" and "new_present_reading" not in kw:
			values["new_present_reading"] = 3881
		return frappe.get_doc(values).insert()

	def approve_revision(self, rev, maker=BILLING_OFFICER, approver=BILLING_MANAGER):
		act(rev, "Submit for Approval", maker)
		act(rev, "Approve", approver)
		rev.reload()
		return rev

	def second_category(self):
		cat = ensure("Consumer Category", "Electricity-TST Billing 2", {"category_name": "TST Billing 2", "utility": "Electricity"})
		if not frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": cat, "docstatus": 1}):
			t = frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": cat, "effective_from": "2026-01-01",
			                    "pricing_method": "Flat rate", "flat_rate": 50,
			                    "charges": [{"charge_type": "TST Service", "rate": 1700}, {"charge_type": "TST Aquifer", "rate": 1000}]}).insert()
			approve(t)
		return cat

	# ---- revised bill (BIL-11, WF-01, 06)
	def test_the_preview_shows_what_a_corrected_reading_does_to_the_bill(self):
		rev = self.revision(new_present_reading=3881)  # 240 units, not 264
		self.assertEqual((rev.old_units, rev.new_units, rev.old_current, rev.new_current, rev.difference, rev.new_payable_within_due),
		                 (264, 240, 22_260, 20_580, -1_680, 21_684))
		self.assertEqual((rev.connection, rev.previous_reading, rev.old_present_reading), (self.conn, 3641, 3905))

	def test_an_approved_revision_replaces_the_bill_under_the_same_reference_number(self):
		old = self.bill_doc
		with today("2026-11-10"):
			rev = self.approve_revision(self.revision())
		new = frappe.get_doc("Utility Bill", rev.revised_bill)
		old.reload()
		self.assertEqual((new.status, new.docstatus, new.revision_no, new.revises, new.connection, new.current_amount, new.units),
		                 ("Posted", 1, 2, old.name, self.conn, 20_580, 240))
		self.assertEqual((old.status, old.docstatus, old.superseded_by), ("Superseded", 2, new.name))  # both stay visible
		self.assertEqual((str(new.issue_date), str(new.due_date), new.arrears), (str(old.issue_date), str(old.due_date), 1_104))
		self.assertEqual(self.balance(), 20_580 + 1_104)  # the earlier version left the account
		e_old, e_new = frappe.get_doc("Customer Ledger Entry", old.ledger_entry), frappe.get_doc("Customer Ledger Entry", new.ledger_entry)
		self.assertTrue(e_old.reversed_by)
		self.assertEqual((e_new.entry_type, e_new.voucher_no), ("Bill", new.name))
		reading = frappe.get_doc("Meter Reading", old.meter_reading)
		self.assertEqual((reading.present_reading, reading.units, reading.corrected_from), (3881, 240, 3905))

	def test_an_approved_revision_is_listed_with_the_adjustments(self):
		"""Increment 64 (user, 2026-11-28): an approved bill revision didn't show in Adjustments by Reason and Approver."""
		with today("2026-11-10"):
			rev = self.approve_revision(self.revision())
		wide = {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31"}
		_c, data, *_x = mis.run("adjustments_register", wide)
		row = next(r for r in data if r["name"] == rev.name)
		self.assertEqual((row["doctype"], row["note_type"], row["against_bill"], row["amount"], row["signed"], row["reason_code"], row["maker"],
		                  row["approved_by"]), ("Bill Revision", "Revised Bill", self.bill_doc.name, 1_680, -1_680, "READ-WRONG", BILLING_OFFICER, BILLING_MANAGER))
		self.assertIn("units 264.0 → 240.0", row["remarks"])
		self.assertEqual(data[-1]["signed"], -1_680)  # the net effect counts it
		self.assertEqual([r["name"] for r in mis.run("adjustments_register", {**wide, "note_type": "Revised Bill"})[1] if r.get("doctype")], [rev.name])
		self.assertEqual(mis.run("adjustments_register", {**wide, "note_type": "Credit Note"})[1], [])

	def test_payment_channels_show_the_latest_version(self):
		with today("2026-11-10"):
			rev = self.approve_revision(self.revision())
			info = account.enquiry(self.conn)
		self.assertEqual((info["bill"], info["payable_within_due"]), (rev.revised_bill, 21_684))

	def test_the_revised_bill_prints_as_revised_and_the_old_one_reprints_as_it_was(self):
		with today("2026-11-10"):
			rev = self.approve_revision(self.revision())
		self.assertIn("Revised, version 2", bill_print.bill_html(rev.revised_bill))
		old_html = bill_print.bill_html(self.bill_doc.name)
		self.assertIn("22,260", old_html)  # the earlier version is still reproducible exactly (FS-RUN-09)

	def test_a_corrected_category_re_prices_the_bill(self):
		cat = self.second_category()
		with today("2026-11-10"):
			rev = self.approve_revision(self.revision(kind="Corrected category", new_category=cat, reason_code="CAT-WRONG"))
		new = frappe.get_doc("Utility Bill", rev.revised_bill)
		self.assertEqual((new.consumer_category, new.units, new.current_amount), (cat, 264, 264 * 50 + 1_080 + 1_700 + 1_000))
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "consumer_category"), cat)

	def test_a_revision_is_refused_once_the_account_has_moved_on(self):
		self.pay(5_000, "2026-11-10")
		with today("2026-11-10"):
			rev = self.revision()
			with self.assertRaises(Exception) as cm:
				act(rev, "Submit for Approval", BILLING_OFFICER)
		self.assertIn("credit or debit note", str(cm.exception))

	def test_a_revision_is_refused_after_the_due_date_or_for_an_older_bill(self):
		with today("2026-11-16"):
			rev = self.revision()
			with self.assertRaises(Exception) as cm:
				act(rev, "Submit for Approval", BILLING_OFFICER)
			self.assertIn("due date", str(cm.exception))
		sept = frappe.get_doc("Utility Bill", {"connection": self.conn, "billing_month": "2026-09-01", "docstatus": 1})
		with today("2026-10-10"):
			with self.assertRaises(Exception) as cm:
				act(self.revision(sept), "Submit for Approval", BILLING_OFFICER)
		self.assertIn("newer bill", str(cm.exception))

	def test_one_revision_at_a_time_and_the_correction_must_make_sense(self):
		self.revision()
		with self.assertRaises(frappe.ValidationError):
			self.revision()  # one is already open for this bill
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Bill Revision", "original_bill": self.bill_doc.name, "kind": "Corrected reading", "reason_code": "READ-WRONG",
			                "remarks": "lower than the previous reading", "new_present_reading": 3000}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Bill Revision", "original_bill": self.bill_doc.name, "kind": "Corrected category",
			                "reason_code": "CAT-WRONG", "remarks": "same category as billed", "new_category": self.cat}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Bill Revision", "original_bill": self.bill_doc.name, "kind": "Corrected reading", "reason_code": "NON-PAY",
			                "remarks": "not a revision reason", "new_present_reading": 3881}).insert()

	# The October test bill is due 15-11-2026; this test needs "today" before that, so the date is pinned.
	@patch("utility_billing.utility_billing.corrections.nowdate", new=lambda: "2026-11-10")
	def test_the_maker_can_not_approve_and_an_approved_revision_can_not_be_undone(self):
		rev = self.revision()
		act(rev, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(Exception):
			act(rev, "Approve", BILLING_MANAGER)  # maker-checker
		with today("2026-11-10"):
			act(rev, "Approve", BILLING_MANAGER_2)
		rev.reload()
		self.assertEqual(rev.docstatus, 1)
		with self.assertRaises(Exception):
			act(rev, "Cancel", BILLING_MANAGER_2)

	def test_the_bill_a_revision_replaced_can_not_be_revised_again(self):
		with today("2026-11-10"):
			self.approve_revision(self.revision())
			with self.assertRaises(frappe.ValidationError):
				self.revision(self.bill_doc)  # it was replaced; the new version can be revised instead
			newer = frappe.get_doc("Utility Bill", self.bill_doc.superseded_by or frappe.db.get_value("Utility Bill", self.bill_doc.name, "superseded_by"))
			rev2 = self.approve_revision(self.revision(newer, new_present_reading=3891))
		self.assertEqual(frappe.get_doc("Utility Bill", rev2.revised_bill).revision_no, 3)

	# ---- reversing a run needs a reason (RUN-06)
	def test_reversing_a_run_needs_a_reason(self):
		with self.assertRaises(Exception) as cm:
			act(self.run, "Cancel", BILLING_MANAGER)
		self.assertIn("reason", str(cm.exception))
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			approval.set_cancellation_reason("Bill Run", self.run.name, "no")
		approval.set_cancellation_reason("Bill Run", self.run.name, "Wrong readings were used for the run")
		frappe.set_user("Administrator")
		act(self.run, "Cancel", BILLING_MANAGER)
		self.run.reload()
		self.assertEqual((self.run.docstatus, self.run.cancellation_reason), (2, "Wrong readings were used for the run"))
		self.assertEqual(self.balance(), 1_104)

	# ---- due date extension (WF-03)
	def extension(self, new_due, user=BILLING_MANAGER, **kw):
		frappe.set_user(user)
		try:
			return frappe.get_doc({"doctype": "Due Date Extension", "connection": self.conn, "new_due_date": new_due, "reason_code": "EXT-HARDSHIP", **kw}).insert()
		finally:
			frappe.set_user("Administrator")

	def test_a_due_date_extension_moves_the_deadline_and_the_surcharge_follows(self):
		with today("2026-11-10"):
			ext = self.extension("2026-11-20")
		self.assertEqual((str(ext.old_due_date), ext.days_extended, ext.bill, ext.granted_by), ("2026-11-15", 5, self.bill_doc.name, BILLING_MANAGER))
		self.assertIn("falls due after the new due date", ext.surcharge_action)
		self.bill_doc.reload()
		# the issued bill itself is untouched (FS-BIL-10): it is reissued as a revised bill with the new due date (increment 37)
		self.assertEqual((str(self.bill_doc.extended_due_date), str(self.bill_doc.due_date), self.bill_doc.status), ("2026-11-20", "2026-11-15", "Superseded"))
		new = frappe.get_doc("Utility Bill", ext.revised_bill)
		self.assertEqual((new.revises, new.revision_no, str(new.issue_date), str(new.due_date), new.extended_due_date, new.current_amount, new.arrears,
		                  new.payable_within_due, new.surcharge_amount, new.other_since_issue, new.ledger_entry),
		                 (self.bill_doc.name, 2, "2026-11-10", "2026-11-20", None, 22_260, 1_104, 23_364, 1_781, 0, self.bill_doc.ledger_entry))
		self.assertIn("Due date extended to", new.reissue_reason)
		self.assertEqual((account.enquiry(self.conn)["due_date"], account.enquiry(self.conn)["bill"]), ("2026-11-20", new.name))
		self.assertIn("SUPERSEDED", bill_print.bill_html(self.bill_doc.name))  # the earlier version reprints, marked superseded
		self.assertIn("(Revised, version 2)", bill_print.bill_html(new.name))
		self.assertEqual(account.apply_surcharges(as_of="2026-11-16", society=SOC), 0)  # not due yet
		self.assertEqual(account.apply_surcharges(as_of="2026-11-21", society=SOC), 1)  # after the new date

	def test_a_superseded_bill_prints_in_the_print_view_and_other_cancelled_documents_do_not(self):
		"""User, 2026-11-21: "allow print of superseded bills in the print view"."""
		from utility_billing.install import allow_superseded_bill_print  # noqa: PLC0415

		allow_superseded_bill_print()
		self.assertEqual(frappe.db.get_single_value("Print Settings", "allow_print_for_cancelled"), 1)
		with today("2026-11-10"):
			ext = self.extension("2026-11-20")
		html = frappe.get_print("Utility Bill", self.bill_doc.name)  # the print view's own path (the bill's print format)
		self.assertIn("SUPERSEDED", html)
		self.assertIn(self.bill_doc.name, html)
		frappe.db.set_value("Utility Bill", self.bill_doc.name, "status", "Cancelled")  # a bill cancelled with its run, not superseded
		with self.assertRaises(frappe.PermissionError):
			frappe.get_print("Utility Bill", self.bill_doc.name)
		self.assertIn("(Revised, version 2)", frappe.get_print("Utility Bill", ext.revised_bill))  # posted bills print as before

	def test_a_surcharge_already_charged_is_reversed_and_charged_again_later(self):
		self.assertEqual(account.apply_surcharges(as_of="2026-11-16", society=SOC), 1)
		with_surcharge = self.balance()
		with today("2026-11-17"):
			ext = self.extension("2026-11-22")
		self.assertIn("reversed", ext.surcharge_action)
		self.assertEqual(frappe.db.get_value("Utility Bill", ext.revised_bill, ["surcharge_status", "surcharge_entry", "due_date"]),
		                 ("Not due yet", None, frappe.utils.getdate("2026-11-22")))
		self.assertEqual(self.balance(), with_surcharge - self.bill_doc.surcharge_amount)
		self.assertEqual(account.apply_surcharges(as_of="2026-11-23", society=SOC), 1)
		self.assertEqual(self.balance(), with_surcharge)

	def test_extension_limits_by_days_and_times_a_year_and_who_may_exceed_them(self):
		with today("2026-11-10"):
			with self.assertRaises(frappe.ValidationError) as cm:
				self.extension("2026-11-23")  # 8 days: the society's limit is 7
			self.assertIn("General Manager", str(cm.exception))
			self.assertEqual(self.extension("2026-11-23", user=GENERAL_MANAGER).days_extended, 8)
			self.extension("2026-11-25")  # +2 days from 11-23, the Billing Manager's second this year
			with self.assertRaises(frappe.ValidationError):
				self.extension("2026-11-26")  # a third in the year
			self.assertEqual(self.extension("2026-11-27", user=GENERAL_MANAGER).days_extended, 2)

	def test_an_extension_needs_something_owed_and_a_later_date(self):
		with today("2026-11-10"):
			with self.assertRaises(frappe.ValidationError):
				self.extension("2026-11-15")
			with self.assertRaises(frappe.ValidationError):
				self.extension("2026-11-12")
			self.pay(30_000, "2026-11-10")
			with self.assertRaises(frappe.ValidationError) as cm:
				self.extension("2026-11-18")
		self.assertIn("owes nothing", str(cm.exception))

	def test_an_extension_can_not_be_edited_and_needs_a_due_date_extension_reason(self):
		with today("2026-11-10"):
			ext = self.extension("2026-11-18")
			ext.remarks = "changed"
			with self.assertRaises(frappe.ValidationError):
				ext.save()
			with self.assertRaises(frappe.ValidationError):
				self.extension("2026-11-25", reason_code="NON-PAY")

	# ---- bill dispute (WF-10)
	def dispute(self, amount=5_000, user=BILLING_OFFICER, bill=None):
		frappe.set_user(user)
		try:
			return frappe.get_doc({"doctype": "Bill Dispute", "connection": self.conn, "bill": (bill or self.bill_doc).name, "disputed_amount": amount,
			                       "reason": "The consumer says the meter was running slow"}).insert()
		finally:
			frappe.set_user("Administrator")

	def test_a_dispute_holds_the_surcharge_until_it_is_decided(self):
		self.assertEqual(account.apply_surcharges(as_of="2026-11-16", society=SOC), 1)
		before = self.balance()
		d = self.dispute()
		self.assertEqual(self.balance(), before - self.bill_doc.surcharge_amount)  # a surcharge already charged is reversed
		self.assertEqual(account.apply_surcharges(as_of="2026-11-30", society=SOC), 0)  # and not charged while the dispute is open
		frappe.set_user(BILLING_MANAGER)
		d.resolve("Released", "The meter test showed it is accurate; the bill stands")
		frappe.set_user("Administrator")
		d.reload()
		self.assertEqual((d.status, d.resolved_by), ("Released", BILLING_MANAGER))
		self.assertEqual(account.apply_surcharges(as_of="2026-11-30", society=SOC), 1)  # the hold is released
		self.assertEqual(self.balance(), before)

	def test_money_in_dispute_is_left_out_of_recovery(self):
		s = frappe.get_doc("Society", SOC)
		s.defaulter_min_amount, s.defaulter_min_days = 10_000, 60
		s.save()
		as_of = "2026-12-20"
		self.assertEqual(len(recovery.accounts(SOC, as_of)), 1)
		self.dispute(amount=20_000)  # leaves 3,364: below the defaulter amount
		self.assertEqual(recovery.accounts(SOC, as_of), [])
		d = frappe.get_doc("Bill Dispute", {"connection": self.conn})
		frappe.set_user(BILLING_MANAGER)
		d.resolve("Withdrawn", "The consumer withdrew the dispute")
		frappe.set_user("Administrator")
		self.assertEqual(len(recovery.accounts(SOC, as_of)), 1)

	def test_a_dispute_is_settled_by_a_revision_or_released_and_only_managers_decide(self):
		d = self.dispute()
		with self.assertRaises(frappe.PermissionError):
			frappe.set_user(BILLING_OFFICER)
			d.resolve("Released", "I am only an officer, not allowed")
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			d.resolve("Released", "no")  # reason too short
		with self.assertRaises(frappe.ValidationError):
			d.resolve("Bill revised", "Settled by a corrected reading")  # needs the approved revision
		with today("2026-11-10"):
			frappe.set_user("Administrator")
			rev = self.approve_revision(self.revision())
		frappe.set_user(BILLING_MANAGER)
		d.reload()
		d.resolve("Bill revised", "The reading was corrected and the bill revised", rev.name)
		frappe.set_user("Administrator")
		d.reload()
		self.assertEqual((d.status, d.revision), ("Bill revised", rev.name))
		with self.assertRaises(frappe.ValidationError):
			d.resolve("Released", "decided twice, should refuse")

	def test_dispute_rules(self):
		self.dispute()
		with self.assertRaises(frappe.ValidationError):
			self.dispute()  # one open dispute per bill
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Bill Dispute", "connection": self.conn, "bill": self.bill_doc.name, "disputed_amount": 99_999, "reason": "more than the bill"}).insert()

	# ---- bulk adjustment (WF-12)
	def bulk(self, amount=500, **kw):
		return frappe.get_doc({"doctype": "Bulk Adjustment", "society": SOC, "audience": "Whole society", "note_type": "Credit Note", "bucket": "Current bill",
		                       "amount": amount, "reason_code": "OUTAGE-COMP", "remarks": "Compensation for the 20-hour outage", **kw}).insert()

	def test_a_bulk_credit_is_approved_by_a_general_manager_and_posts_to_each_account(self):
		ba = self.bulk(500)
		self.assertEqual((ba.accounts, ba.total_amount, [r.result for r in ba.rows]), (1, 500, ["Will post"]))
		with self.assertRaises(Exception):
			act(ba, "Submit for Approval", BILLING_OFFICER)  # a Billing Manager makes these
		act(ba, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(Exception):
			act(ba, "Approve", BILLING_MANAGER)
		before = self.balance()
		act(ba, "Approve", GENERAL_MANAGER)
		ba.reload()
		self.assertEqual((ba.docstatus, ba.accounts, ba.rows[0].result), (1, 1, "Posted"))
		e = frappe.get_doc("Customer Ledger Entry", ba.rows[0].ledger_entry)
		self.assertEqual((e.entry_type, e.voucher_type, e.voucher_no, e.amount, e.d_current), ("Credit Note", "Bulk Adjustment", ba.name, -500, -500))
		self.assertEqual(self.balance(), before - 500)
		act(ba, "Cancel", GENERAL_MANAGER)
		self.assertEqual(self.balance(), before)

	def test_a_bulk_debit_and_the_audience_filters(self):
		ba = self.bulk(100, note_type="Debit Note")
		self.assertEqual(ba.accounts, 1)
		frappe.db.set_value("Property", self.prop, "block", "N Block")
		self.assertEqual(self.bulk(100, audience="Block", block="N Block").accounts, 1)
		self.assertEqual(self.bulk(100, audience="Block", block="Z Block").accounts, 0)
		self.assertEqual(self.bulk(100, audience="Category", consumer_category=self.cat).accounts, 1)
		with self.assertRaises(frappe.ValidationError):
			self.bulk(100, audience="Block")  # needs the block

	def test_a_credit_larger_than_the_balance_skips_the_account_and_an_empty_list_cant_be_submitted(self):
		ba = self.bulk(30_000)  # the current bill is only 22,260
		self.assertEqual((ba.accounts, ba.rows[0].result), (0, "Skipped: nothing to credit"))
		with self.assertRaises(Exception) as cm:
			act(ba, "Submit for Approval", BILLING_MANAGER)
		self.assertIn("No account", str(cm.exception))

	def test_bulk_adjustment_fields_are_checked(self):
		with self.assertRaises(frappe.ValidationError):
			self.bulk(0)
		with self.assertRaises(frappe.ValidationError):
			self.bulk(100, reason_code="NON-PAY")
		with self.assertRaises(frappe.ValidationError):
			self.bulk(100, remarks="no")

	# ---- dishonoured cheque (WF-11)
	def cheque(self, txn="C1", amount=1_000):
		return self._cheque(txn, amount)

	def _cheque(self, txn, amount):
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Bank / Channel", "payment_date": "2026-11-10",
		                    "amount": amount, "mode": "Cheque", "instrument_no": f"CHQ-{txn}", "channel_txn_id": txn}).insert()
		r.submit()
		return r

	def reverse(self, receipt):
		frappe.set_user(BILLING_MANAGER)
		try:
			return account.reverse_document("Payment Receipt", receipt.name, "CHQ-BOUNCE", "Cheque returned unpaid by the bank")
		finally:
			frappe.set_user("Administrator")

	def test_a_dishonoured_cheque_charges_the_fee_and_allows_cash_only(self):
		fee_type = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Cheque Dishonour", "amount": 500}).insert()
		frappe.db.set_value("Society", SOC, {"cheque_dishonour_fee": fee_type.name, "cash_only_after_dishonour": 1})
		receipt = self.cheque()
		before = self.balance()
		self.reverse(receipt)
		fee = frappe.get_doc("Fee Charge", {"source_name": receipt.name})
		self.assertEqual((fee.fee_type, fee.amount, fee.docstatus, fee.source_type), (fee_type.name, 500, 1, "Payment Receipt"))
		self.assertEqual(self.balance(), before + 1_000 + 500)  # the payment is restored and the fee added
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		self.assertEqual(frappe.db.get_value("Consumer", consumer, "cash_only"), 1)
		with self.assertRaises(frappe.ValidationError) as cm:
			self.cheque("C2")
		self.assertIn("cash only", str(cm.exception))
		self.pay(100, "2026-11-10", txn="BT1")  # a bank transfer is still fine

	def test_nothing_extra_happens_when_the_society_set_no_dishonour_rule(self):
		receipt = self.cheque("C3")
		before = self.balance()
		self.reverse(receipt)
		self.assertEqual(self.balance(), before + 1_000)
		self.assertFalse(frappe.db.exists("Fee Charge", {"source_name": receipt.name}))
		self.assertEqual(frappe.db.get_value("Consumer", frappe.db.get_value("Connection", self.conn, "consumer"), "cash_only"), 0)

	def test_other_reversal_reasons_do_not_trigger_the_dishonour_rule(self):
		fee_type = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Cheque Dishonour", "amount": 500}).insert()
		frappe.db.set_value("Society", SOC, {"cheque_dishonour_fee": fee_type.name, "cash_only_after_dishonour": 1})
		receipt = self.cheque("C4")
		frappe.set_user(BILLING_MANAGER)
		account.reverse_document("Payment Receipt", receipt.name, "WRONG-ACC", "Entered on the wrong account")
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Fee Charge", {"source_name": receipt.name}))

	# ---- reports, approvals, menu
	def test_the_register_lists_the_corrections_and_pending_approvals_include_new_types(self):
		with today("2026-11-10"):
			rev = self.revision()
			act(rev, "Submit for Approval", BILLING_OFFICER)
			cols, data, *_ = mis.run("pending_approvals", {"society": SOC})
			self.assertIn(("Bill Revision", rev.name, "Billing Manager"), [(r["doctype"], r["document"], r["waiting_for"]) for r in data])
			act(rev, "Approve", BILLING_MANAGER)
			ext = self.extension("2026-11-18")
		self.dispute(bill=frappe.get_doc("Utility Bill", ext.revised_bill))  # the extension reissued the bill again (increment 37)
		self.bulk(500)
		cols, data, *_ = mis.run("corrections_register", {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31"})
		self.assertEqual(sorted({r["kind"] for r in data}), ["Bill dispute", "Bill revision", "Bulk adjustment", "Due date extension"])
		revision = [r for r in data if r["kind"] == "Bill revision"][0]
		self.assertEqual(revision["amount"], -1_680)
		only = mis.run("corrections_register", {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31", "kind": "Bill dispute"})[1]
		self.assertEqual([r["kind"] for r in only], ["Bill dispute"])

	def test_the_menu_has_the_new_screens(self):
		links = {i.link_to for i in frappe.get_doc("Sidebar", "Utility Management System").items}
		self.assertTrue({"Bill Revision", "Due Date Extension", "Bill Dispute", "Bulk Adjustment", "Corrections Register"} <= links)
