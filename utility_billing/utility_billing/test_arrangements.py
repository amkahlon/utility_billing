# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for installment plans, write-offs and approval levels by amount (increment 15; FS-WF-04, 05, 14, FS-BIL-14, FS-REC-09, FS-ACC-04,
FS-CFG-18). House N 2308 (spec test case 1): after the October bill it owes 22,260 for the month and 1,104 in arrears; 9,000 of
surcharge is added to give the plans and write-offs something to work on."""

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, account_engine as acc, approval, gl, mis, plans, recovery
from utility_billing.utility_billing.doctype.installment_plan import installment_plan as plan_module
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	ACCOUNTS_OFFICER, BILLING_MANAGER, BILLING_OFFICER, GENERAL_MANAGER, MANAGEMENT, RECOVERY_OFFICER, act,
)

TODAY = "2026-11-10"  # the day the plans are made and the down payments paid, before the November bill (issued 14-11)


@contextmanager
def today(date=TODAY):
	with patch.object(plan_module, "nowdate", return_value=date), patch.object(plans, "nowdate", return_value=date):
		yield


class UnitTestArrangementRules(UnitTestCase):
	def test_the_schedule_splits_what_is_left_after_the_down_payment(self):
		rows = plans.schedule(10_104, 2_000, 4, "2026-11-20")
		self.assertEqual([(n, str(m), int(a)) for n, m, a in rows], [(1, "2026-11-01", 2_026), (2, "2026-12-01", 2_026), (3, "2027-01-01", 2_026), (4, "2027-02-01", 2_026)])

	def test_the_last_installment_takes_the_rounding(self):
		rows = plans.schedule(1_000, 0, 3, "2026-11-01")
		self.assertEqual([int(a) for _n, _m, a in rows], [333, 333, 334])
		self.assertEqual(sum(a for _n, _m, a in rows), Decimal(1_000))

	def test_nothing_to_split(self):
		self.assertEqual(plans.schedule(1_000, 1_000, 3, "2026-11-01"), [])
		self.assertEqual(plans.schedule(1_000, 0, 0, "2026-11-01"), [])

	def test_a_write_off_takes_arrears_then_surcharge_then_fees(self):
		bal = {**acc.empty(), "current": acc.D(22_260), "arrears": acc.D(1_104), "surcharge": acc.D(9_000), "fees": acc.D(500)}
		d = acc.write_off(bal, 9_500)
		self.assertEqual((d["arrears"], d["surcharge"], d["fees"], d["current"]), (acc.D(-1_104), acc.D(-8_396), acc.D(0), acc.D(0)))
		d = acc.write_off(bal, 10_604)
		self.assertEqual((d["arrears"], d["surcharge"], d["fees"]), (acc.D(-1_104), acc.D(-9_000), acc.D(-500)))

	def test_a_write_off_never_touches_the_current_bill_or_exceeds_what_is_owed(self):
		bal = {**acc.empty(), "current": acc.D(22_260), "arrears": acc.D(100)}
		with self.assertRaises(ValueError):
			acc.write_off(bal, 101)
		with self.assertRaises(ValueError):
			acc.write_off(bal, 0)


class IntegrationTestArrangements(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		self.run = self.post_october()
		self.october_bill = self.bill(self.run)
		# 9,000 of surcharge on top of the 1,104 of arrears
		account.post(self.conn, "Surcharge", {"surcharge": 9_000}, "Utility Bill", self.october_bill.name, "2026-11-16", "Surcharge for the plans to work on")

	def post_october(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def november(self):
		self.month_reading("2026-11-01", 4100)
		self.fpa("2026-11-01", 1.30)
		return self.bill(self.post(self.make_run("2026-11-01", "2026-11-14")))  # issued on 14-11, due 28-11

	def balances(self):
		return account.balances(self.conn)

	# ---- installment plans (WF-05, BIL-14)
	def plan(self, **kw):
		values = {"doctype": "Installment Plan", "connection": self.conn, "amount_to_split": 10_104, "down_payment": 2_000, "installments": 4,
		          "first_month": "2026-11-01", "remarks": "Agreed with the consumer on a visit", **kw}
		return frappe.get_doc(values).insert()

	def approved_plan(self, **kw):
		with today():
			plan = self.plan(**kw)
			act(plan, "Submit for Approval", BILLING_OFFICER)
			self.pay(2_000, TODAY, txn=frappe.generate_hash(length=8))  # the down payment
			act(plan, "Approve", BILLING_MANAGER)
		plan.reload()
		return plan

	def test_a_plan_shows_its_schedule(self):
		with today():
			plan = self.plan()
		self.assertEqual((plan.owed_now, plan.installment_amount, plan.plan_status, plan.workflow_state), (10_104, 2_026, "Not started", "Draft"))
		self.assertEqual([(r.number, str(r.billing_month), r.amount, r.status) for r in plan.schedule],
		                 [(1, "2026-11-01", 2_026, "Scheduled"), (2, "2026-12-01", 2_026, "Scheduled"), (3, "2027-01-01", 2_026, "Scheduled"), (4, "2027-02-01", 2_026, "Scheduled")])

	def test_the_terms_follow_the_societys_rules(self):
		with today():
			for bad, word in (({"installments": 1}, "2 to 6"), ({"installments": 7}, "2 to 6"), ({"down_payment": 500}, "at least 10%"),
			                  ({"down_payment": 10_104}, "less than"), ({"amount_to_split": 10_105}, "can't be for more"), ({"amount_to_split": 0}, "Enter the amount"),
			                  ({"first_month": "2026-10-01"}, "before the plan")):
				with self.assertRaises(frappe.ValidationError, msg=str(bad)) as cm:
					self.plan(**bad)
				self.assertIn(word, str(cm.exception))
			self.plan()
			with self.assertRaises(frappe.ValidationError) as cm:
				self.plan()
			self.assertIn("already an open plan", str(cm.exception))

	def test_the_down_payment_must_be_received_before_approval(self):
		with today():
			plan = self.plan()
			act(plan, "Submit for Approval", BILLING_OFFICER)
			with self.assertRaises(Exception) as cm:
				act(plan, "Approve", BILLING_MANAGER)
			self.assertIn("down payment", str(cm.exception))
			self.pay(1_999, TODAY, txn="DP1")
			with self.assertRaises(Exception):
				act(plan, "Approve", BILLING_MANAGER)  # not enough
			self.pay(1, TODAY, txn="DP2")
			act(plan, "Approve", BILLING_MANAGER)
		plan.reload()
		self.assertEqual((plan.docstatus, plan.plan_status), (1, "Active"))

	def test_a_billing_officer_cannot_approve_and_the_maker_cannot_either(self):
		with today():
			plan = self.plan()
			act(plan, "Submit for Approval", BILLING_MANAGER)
			self.pay(2_000, TODAY, txn="DP3")
			with self.assertRaises(Exception):
				act(plan, "Approve", BILLING_MANAGER)  # maker-checker
			with self.assertRaises(Exception):
				act(plan, "Approve", BILLING_OFFICER)

	def test_each_bill_adds_one_installment_and_only_that_much_of_the_arrears_is_due(self):
		plan = self.approved_plan()
		nov = self.november()
		self.assertEqual((nov.installment_due, nov.installment_no, nov.installment_plan), (2_026, 1, plan.name))
		self.assertEqual((nov.payable_within_due, nov.payable_after_due - nov.payable_within_due), (nov.current_amount + 2_026, nov.surcharge_amount))
		self.assertGreater(nov.arrears, nov.installment_due)  # the rest of the arrears isn't due yet
		row = frappe.get_doc("Installment Plan", plan.name).schedule[0]
		self.assertEqual((row.status, row.bill), ("Billed", nov.name))
		from utility_billing.utility_billing import bill_print

		self.assertIn("Installment due with this bill", bill_print.bill_html(nov.name))

	def test_the_counter_asks_for_the_bill_not_the_whole_balance(self):
		self.approved_plan()
		nov = self.november()
		info = account.enquiry(self.conn)
		self.assertEqual(info["amount_due"], nov.payable_within_due)
		self.assertGreater(info["balance"], info["amount_due"])
		self.pay(1_000, "2026-11-14", txn="PART")
		self.assertEqual(account.enquiry(self.conn)["amount_due"], nov.payable_within_due - 1_000)

	def test_a_paid_installment_is_marked_paid_and_a_missed_one_cancels_the_plan(self):
		plan = self.approved_plan()
		nov = self.november()
		self.pay(nov.payable_within_due, str(nov.issue_date), txn="FULL")
		self.assertEqual(plans.daily("2026-12-20", SOC), (1, 0))
		rows = {r.number: r.status for r in frappe.get_doc("Installment Plan", plan.name).schedule}
		self.assertEqual((rows[1], rows[2]), ("Paid", "Scheduled"))
		self.assertEqual(plans.active_plan(self.conn), plan.name)

	def test_a_missed_installment_cancels_the_plan_and_the_full_arrears_fall_due_again(self):
		plan = self.approved_plan()
		self.november()
		self.assertEqual(plans.daily("2026-11-20", SOC), (0, 0))  # before the due date (28-11): nothing yet
		self.assertEqual(plans.daily("2026-12-20", SOC), (0, 1))
		plan.reload()
		self.assertEqual(plan.plan_status, "Defaulted")
		self.assertEqual([r.status for r in plan.schedule], ["Missed", "Cancelled", "Cancelled", "Cancelled"])
		self.assertIsNone(plans.active_plan(self.conn))
		self.assertIsNone(plans.installment_for(self.conn, "2026-12-01"))  # the next bill asks for all the arrears

	def test_an_extended_due_date_gives_more_time_to_pay_the_installment(self):
		self.approved_plan()
		nov = self.november()
		frappe.db.set_value("Utility Bill", nov.name, "extended_due_date", "2026-12-30")
		self.assertEqual(plans.daily("2026-12-20", SOC), (0, 0))  # still within the extended due date

	def test_a_plan_can_be_cancelled_by_a_general_manager_and_the_rest_is_cancelled(self):
		plan = self.approved_plan()
		act(plan, "Cancel", GENERAL_MANAGER)
		plan.reload()
		self.assertEqual((plan.docstatus, plan.plan_status), (2, "Cancelled"))
		self.assertEqual({r.status for r in plan.schedule}, {"Cancelled"})
		self.assertIsNone(plans.installment_for(self.conn, "2026-12-01"))

	def test_an_account_on_a_plan_is_left_out_of_recovery(self):
		s = frappe.get_doc("Society", SOC)
		s.defaulter_min_amount, s.defaulter_min_days = 5_000, 30
		s.save()
		self.assertIsNone(recovery.accounts(SOC, "2026-12-20")[0]["hold"])
		self.approved_plan()
		row = recovery.accounts(SOC, "2026-12-20")[0]
		self.assertEqual((row["hold"], row["next_stage"]), ("Installment plan active", None))

	def test_an_account_with_no_plan_is_billed_as_before(self):
		nov = self.november()
		self.assertEqual((nov.installment_due, nov.installment_no, nov.installment_plan), (0, 0, None))
		self.assertEqual(nov.payable_within_due, nov.current_amount + nov.arrears)

	# ---- write-offs (REC-09, WF-14)
	def writeoff(self, amount=9_500, **kw):
		return frappe.get_doc({"doctype": "Write Off", "connection": self.conn, "amount": amount, "reason_code": "IRRECOV", "resolution_no": "BR-2026-14",
		                       "resolution_date": "2026-11-10", "remarks": "Three notices and two visits; the consumer has left", **kw}).insert()

	def test_a_write_off_is_approved_by_a_general_manager_and_posts_arrears_first(self):
		wo = self.writeoff()
		self.assertEqual(wo.owed_now, 10_104)
		act(wo, "Submit for Approval", ACCOUNTS_OFFICER)
		with self.assertRaises(Exception) as cm:
			act(wo, "Approve", BILLING_MANAGER)  # the default level is a General Manager
		self.assertIn("General Manager", str(cm.exception))
		before = self.balances()
		act(wo, "Approve", GENERAL_MANAGER)
		wo.reload()
		e = frappe.get_doc("Customer Ledger Entry", wo.ledger_entry)
		self.assertEqual((wo.docstatus, e.entry_type, e.voucher_type, e.amount, e.d_arrears, e.d_surcharge), (1, "Write-off", "Write Off", -9_500, -1_104, -8_396))
		after = self.balances()
		self.assertEqual((after["arrears"], after["surcharge"], after["current"]), (0, before["surcharge"] - 8_396, before["current"]))

	def test_a_write_off_can_not_exceed_what_is_owed_and_needs_the_board_record(self):
		with self.assertRaises(frappe.ValidationError):
			self.writeoff(10_105)
		with self.assertRaises(frappe.ValidationError):
			self.writeoff(0)
		with self.assertRaises(frappe.ValidationError):
			self.writeoff(100, resolution_no="")
		with self.assertRaises(frappe.ValidationError):
			self.writeoff(100, remarks="none")
		with self.assertRaises(frappe.ValidationError):
			self.writeoff(100, reason_code="NON-PAY")

	def test_a_write_off_is_journaled_to_the_bad_debt_account(self):
		for code, name, kind in (("T-1200", "Customer receivables", "Receivable"), ("T-5200", "Bad debts", "Expense")):
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		soc = frappe.get_doc("Society", SOC)
		soc.update({"receivables_account": "T-1200", "write_off_account": "T-5200"})
		soc.save()
		wo = self.writeoff()
		act(wo, "Submit for Approval", ACCOUNTS_OFFICER)
		act(wo, "Approve", GENERAL_MANAGER)
		wo.reload()
		self.assertEqual(sorted(gl.lines_for(wo.ledger_entry)), [("T-1200", 0, 9_500), ("T-5200", 9_500, 0)])
		soc.write_off_account = None
		soc.save()
		with self.assertRaises(gl.MissingAccount):
			gl.lines_for(wo.ledger_entry)

	def test_cancelling_a_write_off_brings_the_debt_back(self):
		wo = self.writeoff()
		before = self.balances()
		act(wo, "Submit for Approval", ACCOUNTS_OFFICER)
		act(wo, "Approve", GENERAL_MANAGER)
		act(wo, "Cancel", GENERAL_MANAGER)
		self.assertEqual(self.balances(), before)

	# ---- approval levels by amount (ACC-04, WF-04)
	def set_levels(self):
		s = frappe.get_doc("Society", SOC)
		s.set("approval_levels", [{"transaction": "Account Adjustment", "up_to_amount": 5_000, "approver_role": "Billing Manager"},
		                          {"transaction": "Account Adjustment", "up_to_amount": 15_000, "approver_role": "General Manager"},
		                          {"transaction": "Account Adjustment", "up_to_amount": 0, "approver_role": "Management"}])
		s.save()

	def waiver(self, amount, bucket="Surcharge"):
		return frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": self.conn, "bucket": bucket, "amount": amount,
		                       "reason_code": "SUR-WAIVE", "remarks": "Waived by management"}).insert()

	def test_without_levels_a_general_manager_approves_adjustments_as_before(self):
		doc = self.waiver(1_000)
		self.assertEqual(approval.required_roles(doc), ["General Manager"])
		act(doc, "Submit for Approval", BILLING_OFFICER)
		with self.assertRaises(Exception):
			act(doc, "Approve", BILLING_MANAGER)
		act(doc, "Approve", GENERAL_MANAGER)

	def test_a_small_surcharge_waiver_is_approved_by_a_billing_manager(self):
		self.set_levels()
		doc = self.waiver(3_000)
		self.assertEqual(approval.required_roles(doc), ["Billing Manager", "General Manager", "Management"])
		act(doc, "Submit for Approval", BILLING_OFFICER)
		act(doc, "Approve", BILLING_MANAGER)
		doc.reload()
		self.assertEqual((doc.docstatus, doc.approved_by), (1, BILLING_MANAGER))

	def test_a_bigger_waiver_needs_a_general_manager_and_a_huge_one_management(self):
		self.set_levels()
		mid = self.waiver(8_000)
		act(mid, "Submit for Approval", BILLING_OFFICER)
		with self.assertRaises(Exception) as cm:
			act(mid, "Approve", BILLING_MANAGER)
		self.assertIn("General Manager or a higher level", str(cm.exception))
		act(mid, "Approve", GENERAL_MANAGER)
		big = self.waiver(20_000, bucket="Current bill")
		self.assertEqual(approval.required_roles(big), ["Management"])
		act(big, "Submit for Approval", BILLING_OFFICER)
		for user in (BILLING_MANAGER, GENERAL_MANAGER):
			with self.assertRaises(Exception):
				act(big, "Approve", user)
		act(big, "Approve", MANAGEMENT)
		big.reload()
		self.assertEqual(big.docstatus, 1)

	def test_a_higher_level_may_approve_a_smaller_amount(self):
		self.set_levels()
		doc = self.waiver(3_000)
		act(doc, "Submit for Approval", BILLING_OFFICER)
		act(doc, "Approve", GENERAL_MANAGER)
		doc.reload()
		self.assertEqual(doc.approved_by, GENERAL_MANAGER)

	def test_levels_apply_to_write_offs_and_plans_too(self):
		s = frappe.get_doc("Society", SOC)
		s.set("approval_levels", [{"transaction": "Write Off", "up_to_amount": 2_000, "approver_role": "Billing Manager"},
		                          {"transaction": "Write Off", "up_to_amount": 0, "approver_role": "General Manager"}])
		s.save()
		self.assertEqual(approval.required_roles(self.writeoff(1_500)), ["Billing Manager", "General Manager"])
		self.assertEqual(approval.required_roles(frappe._dict(doctype="Installment Plan", society=SOC, amount_to_split=50_000)), ["Billing Manager"])

	def test_the_level_table_is_checked(self):
		s = frappe.get_doc("Society", SOC)
		s.set("approval_levels", [{"transaction": "Refund", "up_to_amount": 1_000, "approver_role": "Billing Manager"}])
		s.save()  # a table with no top level is fine: an amount above every limit goes to the highest level
		doc = frappe._dict(doctype="Refund", society=SOC, amount=50_000)
		self.assertEqual(approval.required_roles(doc), ["Billing Manager"])

	# ---- register and menu
	def test_the_corrections_register_lists_plans_and_write_offs(self):
		with today():
			self.plan()
		self.writeoff()
		cols, data, *_ = mis.run("corrections_register", {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31"})
		self.assertEqual({"Installment plan", "Write-off"} <= {r["kind"] for r in data}, True)
		plan = [r for r in data if r["kind"] == "Installment plan"][0]
		self.assertEqual((plan["amount"], plan["detail"]), (10_104, "4 installments after a down payment of 2,000"))
		self.assertEqual([r["amount"] for r in data if r["kind"] == "Write-off"], [-9_500])
		cols, data, *_ = mis.run("pending_approvals", {"society": SOC, "transaction": "Write Off"})
		self.assertEqual({r["doctype"] for r in data}, set())  # a draft isn't waiting for approval yet

	def test_the_menu_has_the_new_screens(self):
		links = {i.link_to for i in frappe.get_doc("Sidebar", "Utility Billing").items}
		self.assertTrue({"Installment Plan", "Write Off"} <= links)
