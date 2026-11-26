# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 58: a bill run is reversed a batch of bills at a time, and a stopped reversal carries on.

The defect: reversing the demo society's November run (21,446 bills) was one database transaction; it went over the database's limit
("Too many changes to database in single action") twice, and on a third try the worker was lost after 14,600 bills. Nothing changed,
and the run could never be reversed."""

from unittest.mock import patch

import frappe

from utility_billing.utility_billing import account, billing, billrun_wizard, bulk, gl, readings
from utility_billing.utility_billing.doctype.bill_run import bill_run
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER

OTHERS = 4  # with the shared fixture's account, five bills: three batches of two


class ReversalCase(BillingCase):
	"""Helpers only (no tests of its own)."""

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
		patches = patches or {"wraps": billing._reverse_group}  # the real group reversal, counted
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


class IntegrationTestRunReversal(ReversalCase):
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


class IntegrationTestReversedRunLeavesNothing(ReversalCase):
	"""Increment 59. The user, after reversing the demo run: "there must not be any impact of previous Bill Run process anywhere in the
	system". The customer accounts were back, but the run's journal (Rs 223.9M) still stood in the general ledger and 21,113 bill messages
	were still queued to be sent."""

	def setUp(self):
		super().setUp()
		from utility_billing.utility_billing.test_gl import ACCOUNTS, CHARGE_ACCOUNTS  # noqa: PLC0415

		for code, name, kind in ACCOUNTS:
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		for ct, acct in CHARGE_ACCOUNTS.items():
			frappe.db.set_value("Charge Type", ct, "account_code", acct)
		self.map_receivables("T-1200")

	def map_receivables(self, acct):
		soc = frappe.get_doc("Society", SOC)
		soc.update({"receivables_account": acct, "surcharge_income_account": "T-4900", "other_income_account": "T-4990",
		            "adjustments_account": "T-5100"})
		soc.set("payment_accounts", [{"mode": "Cash", "account": "T-1010"}])
		soc.save()

	def lines(self, journal):
		return {l.account: round(l.debit - l.credit, 2) for l in frappe.get_doc("GL Journal", journal).lines}

	def reversal_entries(self, run):
		return frappe.db.sql("""select e.name, e.gl_journal from `tabCustomer Ledger Entry` e join `tabUtility Bill` b on b.name = e.voucher_no
			where e.voucher_type = 'Utility Bill' and e.entry_type = 'Reversal' and b.bill_run = %s""", run.name, as_dict=True)

	def test_the_run_journal_is_taken_back_out_of_the_general_ledger(self):
		run = self.posted_run()
		journal = frappe.db.get_value("Bill Run", run.name, "gl_journal")
		self.assertTrue(journal)
		self.reverse(run)
		rev = frappe.db.get_value("Bill Run", run.name, "reversal_journal")
		self.assertTrue(rev)
		j, r = frappe.get_doc("GL Journal", journal), frappe.get_doc("GL Journal", rev)
		self.assertEqual((r.journal_type, r.bill_run, r.entry_count), ("Bill Run Reversal", run.name, OTHERS + 1))
		self.assertIn("Wrong readings", r.narration)
		self.assertEqual(self.lines(rev), {a: -v for a, v in self.lines(journal).items()})  # the exact opposite, account by account
		self.assertEqual(r.receivables_change, -j.receivables_change)
		self.assertEqual({e.gl_journal for e in self.reversal_entries(run)}, {rev})
		# Make Journals afterwards: nothing more to make, no daily journal takes the reversals a second time
		self.assertEqual(gl.make_journals_for(SOC), [])

	def test_a_run_reversed_before_its_journal_was_made_nets_out(self):
		self.map_receivables(None)  # no receivables account: the run's journal waits (as at posting on a site not yet mapped)
		run = self.posted_run()
		self.assertFalse(frappe.db.get_value("Bill Run", run.name, "gl_journal"))
		self.reverse(run)
		self.assertFalse(frappe.db.get_value("Bill Run", run.name, "reversal_journal"))  # still not mapped: it waits too
		self.map_receivables("T-1200")
		made = gl.make_journals_for(SOC)
		rev = frappe.db.get_value("Bill Run", run.name, "reversal_journal")
		self.assertEqual(made, [rev])  # one journal: no run journal for a reversed run, no daily journal of its reversals
		r = frappe.get_doc("GL Journal", rev)
		self.assertEqual((r.entry_count, r.total_debit, r.total_credit, r.receivables_change), (2 * (OTHERS + 1), 0, 0, 0))
		self.assertFalse(frappe.db.sql("""select count(*) from `tabCustomer Ledger Entry` e join `tabUtility Bill` b on b.name = e.voucher_no
			where b.bill_run = %s and ifnull(e.gl_journal, '') = ''""", run.name)[0][0])

	def message(self, bill, status):
		return frappe.get_doc({"doctype": "Message Log", "event": "bill.issued", "channel": "SMS", "status": status, "society": SOC,
		                       "connection": bill.connection, "recipient": "03001234567", "body": "Your bill",
		                       "reference_doctype": "Utility Bill", "reference_name": bill.name}).insert(ignore_permissions=True).name

	def test_messages_not_yet_sent_are_cancelled(self):
		run = self.posted_run()
		bills = frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["name", "connection"], order_by="name")
		queued = [self.message(b, "Queued") for b in bills[:3]]
		sent = self.message(bills[3], "Sent")
		self.reverse(run)
		for name in queued:
			m = frappe.db.get_value("Message Log", name, ["status", "error"], as_dict=True)
			self.assertEqual(m.status, "Cancelled")
			self.assertIn(f"bill run {run.name} was reversed", m.error)
		self.assertEqual(frappe.db.get_value("Message Log", sent, "status"), "Sent")  # what was sent stays on record
		info = billrun_wizard.load(run.name)["reversed"]
		self.assertEqual(info["messages_cancelled"], 3)

	def test_the_wizard_shows_a_reversed_run_and_starts_the_month_again(self):
		run = self.posted_run()
		self.assertIsNone(billrun_wizard.load(run.name)["reversed"])
		self.reverse(run)
		info = billrun_wizard.load(run.name)["reversed"]
		self.assertEqual((info["bills"], info["reason"], info["next_run"]), (OTHERS + 1, "Wrong readings: the run is reversed", None))
		self.assertTrue(info["reversal_journal"])
		frappe.set_user(BILLING_OFFICER)
		out = billrun_wizard.start_again(run.name)
		frappe.set_user("Administrator")
		self.assertTrue(out["new"])
		new = frappe.get_doc("Bill Run", out["name"])
		self.assertEqual((new.name, new.amended_from, new.docstatus), (f"{run.name}-1", run.name, 0))
		self.assertEqual((str(new.billing_month), str(new.issue_date), new.reading_cycle), (str(run.billing_month), str(run.issue_date), run.reading_cycle))
		# nothing of the reversed run is carried over: not posted, not reversed, no journal, no totals
		self.assertEqual([new.get(f) or None for f in ("posting_status", "posting_error", "reversal_status", "gl_journal", "reversal_journal",
		                                                "cancellation_reason", "bill_count")], [None] * 7)
		self.assertNotEqual(new.calculation_status, "Calculated")
		billing.calculate_run(new.name)
		self.assertEqual(frappe.db.count("Utility Bill", {"bill_run": new.name, "docstatus": 0}), OTHERS + 1)
		self.assertEqual(billrun_wizard.load(run.name)["reversed"]["next_run"], new.name)
		self.assertEqual(billrun_wizard.start_again(run.name), {"name": new.name, "new": False})  # once a month
		with self.assertRaises(frappe.ValidationError):
			billrun_wizard.start_again(new.name)  # not reversed
