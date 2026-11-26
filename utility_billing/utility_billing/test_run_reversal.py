# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 58: a bill run is reversed a batch of bills at a time, and a stopped reversal carries on.

The defect: reversing the demo society's November run (21,446 bills) was one database transaction; it went over the database's limit
("Too many changes to database in single action") twice, and on a third try the worker was lost after 14,600 bills. Nothing changed,
and the run could never be reversed."""

from unittest.mock import patch

import frappe

from utility_billing.utility_billing import account, billing, bulk, readings
from utility_billing.utility_billing.doctype.bill_run import bill_run
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER

OTHERS = 4  # with the shared fixture's account, five bills: three batches of two


class IntegrationTestRunReversal(BillingCase):
	def posted_run(self):
		others = []
		for i in range(OTHERS):
			consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": f"TST Reversal Owner {i}"}).insert().name
			prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"RV {i}"}).insert().name
			meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-REV-M{i}", "society": SOC, "utility": "Electricity"}).insert().name
			others.append(frappe.get_doc({
				"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
				"consumer_category": self.cat, "status": "Active",
				"meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 1999}],
			}).insert().name)
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		for n, conn in enumerate(others):
			r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": conn})
			r.present_reading, r.reading_date = 2100 + 10 * n, "2026-10-05"
			r.save()
			if r.status == "Flagged":
				readings.approve_reading(r.name, "Confirmed with the consumer")
		readings.close_cycle(cycle)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		self.assertEqual(frappe.db.count("Utility Bill", {"bill_run": run.name, "docstatus": 1}), OTHERS + 1)
		frappe.db.set_value("Bill Run", run.name, "cancellation_reason", "Wrong readings: the run is reversed")
		self.accounts = [self.conn, *others]
		return run

	def reverse(self, run, batch=2, plain=True, **patches):
		"""The background job's work (bill_run.cancel_in_background) as the Billing Manager, committing every `batch` bills: the commits
		are counted, not made, so the test's own transaction is undone afterwards."""
		frappe.flags.reverse_batch = batch
		frappe.set_user(BILLING_MANAGER)
		try:
			with patch.object(frappe.db, "commit") as commit, patch.object(frappe.db, "rollback"), \
					patch.object(bulk, "plain", return_value=plain), patch.object(billing, "_reverse_group", **patches) as group:
				out = bill_run.cancel_in_background(run.name)
			return out, commit.call_count, group.call_count
		finally:
			frappe.flags.reverse_batch = None
			frappe.set_user("Administrator")

	def assert_all_reversed(self, run):
		bills = frappe.get_all("Utility Bill", filters={"bill_run": run.name},
		                       fields=["name", "docstatus", "status", "outstanding_amount", "ledger_entry", "connection"])
		self.assertEqual(len(bills), OTHERS + 1)
		for b in bills:
			self.assertEqual((b.docstatus, b.status, b.outstanding_amount), (2, "Cancelled", 0), b.name)
			reversals = frappe.get_all("Customer Ledger Entry", filters={"reverses": b.ledger_entry},
			                           fields=["name", "entry_type", "voucher_no", "remarks", "amount"])
			self.assertEqual(len(reversals), 1, b.name)  # reversed once, also across a stop and Reverse Again
			r = reversals[0]
			self.assertEqual((r.entry_type, r.voucher_no, r.remarks), ("Reversal", b.name, "Bill cancelled"))
			self.assertEqual(frappe.db.get_value("Customer Ledger Entry", b.ledger_entry, "reversed_by"), r.name)
			self.assertEqual(round(r.amount, 2), -round(frappe.db.get_value("Customer Ledger Entry", b.ledger_entry, "amount"), 2))
		for conn in self.accounts:
			self.assertEqual(account.balance(conn), 0, conn)  # the accounts owed nothing before the run
		lines = frappe.db.sql("select distinct docstatus from `tabBill Line` where parent in %s", ([b.name for b in bills],), pluck=True)
		self.assertEqual(lines, [2])
		doc = frappe.get_doc("Bill Run", run.name)
		self.assertEqual((doc.docstatus, doc.workflow_state, doc.reversal_status), (2, "Cancelled", "Reversed"))

	def test_a_run_is_reversed_a_batch_at_a_time(self):
		run = self.posted_run()
		out, commits, groups = self.reverse(run, wraps=billing._reverse_group)
		self.assertEqual(out["workflow_state"], "Cancelled")
		self.assertEqual(groups, 3)  # five bills, two at a time, each account's bill reversed in a group
		self.assertGreaterEqual(commits, 4)  # Reversing marked, then after each group
		self.assert_all_reversed(run)

	def test_the_one_by_one_reversal_gives_the_same_accounts(self):
		"""Where something on the site reacts to a bill being saved, every bill is cancelled as a document, as before."""
		run = self.posted_run()
		_out, commits, groups = self.reverse(run, plain=False, wraps=billing._reverse_group)
		self.assertEqual(groups, 0)
		self.assertGreaterEqual(commits, 3)
		self.assert_all_reversed(run)

	def test_a_stopped_reversal_carries_on_with_reverse_again(self):
		run = self.posted_run()
		real, calls = billing._reverse_group, []

		def stops_on_the_second_group(names):
			calls.append(names)
			if len(calls) == 2:
				raise frappe.ValidationError("The database connection was lost")
			return real(names)

		with self.assertRaises(frappe.ValidationError) as e:
			self.reverse(run, side_effect=stops_on_the_second_group)
		self.assertIn("3 of 5 bills still posted (The database connection was lost)", str(e.exception))
		self.assertIn("Reverse Again", str(e.exception))
		doc = frappe.get_doc("Bill Run", run.name)
		self.assertEqual((doc.docstatus, doc.workflow_state, doc.reversal_status), (1, "Approved", "Reversal stopped"))
		self.assertIn("Reverse Again", doc.reversal_error)
		self.assertEqual(frappe.db.count("Utility Bill", {"bill_run": run.name, "docstatus": 1}), 3)
		# Reverse Again: the three bills still posted, not the two already reversed
		_out, _commits, groups = self.reverse(run, wraps=billing._reverse_group)
		self.assertEqual(groups, 2)
		self.assert_all_reversed(run)

	def test_nothing_is_reversed_when_the_run_cant_be_reversed(self):
		run = self.posted_run()
		frappe.set_user(BILLING_OFFICER)  # the workflow's Cancel is the Billing Manager's
		with self.assertRaises(frappe.PermissionError):
			bill_run.cancel_in_background(run.name)
		frappe.set_user("Administrator")
		frappe.db.set_value("Bill Run", run.name, "cancellation_reason", "")
		with self.assertRaises(frappe.ValidationError) as e:
			self.reverse(run)
		self.assertIn("reason", str(e.exception))
		doc = frappe.get_doc("Bill Run", run.name)
		self.assertEqual((doc.docstatus, doc.reversal_status or ""), (1, ""))
		self.assertEqual(frappe.db.count("Utility Bill", {"bill_run": run.name, "docstatus": 1}), OTHERS + 1)

	def test_a_later_entry_on_one_account_stops_the_group_under_its_lock(self):
		"""A payment can reach an account between the run's check and its batch; the batch then refuses and changes nothing."""
		run = self.posted_run()
		frappe.flags.reverse_batch = None
		bill = frappe.get_doc("Utility Bill", {"bill_run": run.name, "connection": self.accounts[1]})
		other = frappe.db.get_value("Utility Bill", {"bill_run": run.name, "connection": self.accounts[2]})  # any voucher but the bill
		account.lock(bill.connection)
		entry = account.post(bill.connection, "Fee", {"fees": account.engine.D(50)}, "Utility Bill", other, "2026-11-02", "A fee")
		with self.assertRaises(frappe.ValidationError) as e:
			billing._reverse_group([bill.name])
		self.assertIn("Reverse it first", str(e.exception))
		self.assertIn(entry.voucher_no, str(e.exception))
		self.assertEqual(frappe.db.get_value("Utility Bill", bill.name, "docstatus"), 1)
