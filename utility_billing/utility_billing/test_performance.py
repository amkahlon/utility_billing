# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""The performance step (FS-RUN-07): a bill run reads and writes its accounts a group at a time. These tests hold the fast way to the
one-document-at-a-time way: the same run done both ways must leave the same bills, lines, print snapshots and ledger entries."""

import json
from unittest.mock import patch

import frappe

from utility_billing.utility_billing import account, billing, bulk, readings, runcache
from utility_billing.utility_billing.test_billing import SOC, BillingCase

SKIP = {"name", "creation", "modified", "modified_by", "owner", "parent", "ledger_entry", "print_snapshot", "posted_on", "voucher_no",
        "calculated_on"}


def plain(row):
	return {k: (str(v) if v is not None and not isinstance(v, (int, float, str)) else v) for k, v in row.items() if k not in SKIP}


class IntegrationTestBillRunInGroups(BillingCase):
	def setUp(self):
		super().setUp()
		self.conns = [self.conn]
		for i in range(1, 5):
			consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": f"Group Consumer {i}", "mobile_no": f"0300123450{i}"}).insert().name
			prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"G {i}", "block": "G"}).insert().name
			meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-GRP-M{i}", "society": SOC, "utility": "Electricity"}).insert().name
			self.conns.append(frappe.get_doc({
				"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
				"consumer_category": self.cat, "status": "Active",
				"meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 1999}]}).insert().name)

	def read_month(self, month, present):
		cycle = f"{SOC}-Electricity-{month[:7]}"
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month}).insert()
		readings.create_reading_sheet(cycle)
		for i, conn in enumerate(self.conns):
			r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": conn})
			r.present_reading, r.reading_date = present + i * 37, f"{month[:8]}05"
			r.save()
			if r.status == "Flagged":
				readings.approve_reading(r.name, "Confirmed with the consumer")
		readings.close_cycle(cycle)

	def pay_account(self, conn, amount, date):
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": conn, "payment_channel": "Bank / Channel", "payment_date": date,
		                    "amount": amount, "mode": "Bank Transfer", "channel_txn_id": frappe.generate_hash(length=12)}).insert()
		r.submit()

	def september(self):
		"""August and September read; September posted; three accounts pay (one in part, one too much), two don't."""
		self.read_month("2026-08-01", 2830)
		self.read_month("2026-09-01", 3641)
		self.fpa("2026-09-01", 0)
		sep = self.post(self.make_run("2026-09-01", "2026-10-01"))
		due = {b.connection: b.payable_within_due for b in frappe.get_all("Utility Bill", filters={"bill_run": sep.name},
		                                                                   fields=["connection", "payable_within_due"])}
		self.pay_account(self.conns[0], due[self.conns[0]] - 1104, "2026-10-10")
		self.pay_account(self.conns[1], due[self.conns[1]], "2026-10-12")
		self.pay_account(self.conns[2], due[self.conns[2]] + 500, "2026-10-12")
		frappe.get_doc({"doctype": "Legacy Bill", "connection": self.conns[3], "billing_month": "2026-07-01", "units": 410,
		                "current_amount": 30000, "bill_amount": 30000, "paid_on": "2026-08-10", "paid_amount": 30000}).insert(ignore_permissions=True)
		return sep

	def october(self, post=True):
		run = self.make_run("2026-10-01", "2026-11-01")
		return self.post(run) if post else run

	def state(self, run):
		"""Everything the run wrote, without names and timestamps."""
		out = {"run": (run.bill_count, run.total_units, run.total_current, run.total_arrears, run.total_payable, len(run.problems)),
		       "summary": sorted((r.dimension, r.item, r.bills, r.units, r.amount, r.previous_amount) for r in run.summary)}
		for b in frappe.db.sql("select * from `tabUtility Bill` where bill_run = %s order by connection", run.name, as_dict=True):
			snap = json.loads(b.print_snapshot)
			snap.pop("captured_on")
			entry = frappe.db.sql("select * from `tabCustomer Ledger Entry` where name = %s", b.ledger_entry, as_dict=True)
			out[b.connection] = {
				"bill": plain(b), "snapshot": snap, "entry": [plain(e) for e in entry],
				"has_entry": bool(b.ledger_entry),
				"lines": [plain(r) for r in frappe.db.sql("select * from `tabBill Line` where parent = %s order by idx", b.name, as_dict=True)],
				"taxes": [plain(r) for r in frappe.db.sql("select * from `tabBill Tax` where parent = %s order by idx", b.name, as_dict=True)],
			}
		return out

	def both_ways(self, post):
		frappe.db.savepoint("both_ways")
		fast = self.state(self.october(post))
		frappe.db.rollback(save_point="both_ways")
		frappe.clear_document_cache("Connection")
		with patch.object(bulk, "plain", return_value=False), patch.object(runcache, "prime", return_value=None):
			slow = self.state(self.october(post))
		return fast, slow

	def test_calculated_in_groups_as_one_by_one(self):
		self.september()
		self.read_month("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		fast, slow = self.both_ways(post=False)
		self.assertEqual(fast["run"][0], 5)
		self.assertEqual(fast[self.conn]["bill"]["payable_within_due"], 23364)  # spec test case 1 still comes out
		self.assertEqual(fast, slow)

	def test_posted_in_groups_as_one_by_one(self):
		self.september()
		self.read_month("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		fast, slow = self.both_ways(post=True)
		self.maxDiff = None
		self.assertEqual(fast, slow)
		for conn in self.conns:
			self.assertTrue(fast[conn]["has_entry"])
			self.assertEqual((fast[conn]["bill"]["docstatus"], fast[conn]["bill"]["status"]), (1, "Posted"))
			self.assertEqual({l["docstatus"] for l in fast[conn]["lines"]}, {1})
		# the history printed on the October bills: September from our bills with its payment; July from the previous system
		history = fast[self.conns[3]]["snapshot"]["history"]
		self.assertEqual([(h["month"], h.get("legacy")) for h in history], [("2026-09-01", None), ("2026-07-01", 1)])
		self.assertEqual(fast[self.conns[1]]["snapshot"]["history"][0]["paid_on"], "2026-10-12")
		# arrears as the accounts stood: part paid, paid, overpaid (credit), unpaid
		sep = {c: fast[c]["snapshot"]["history"][0]["billed"] for c in self.conns}
		self.assertEqual([fast[c]["bill"]["arrears"] for c in self.conns], [1104, 0, -500, sep[self.conns[3]], sep[self.conns[4]]])

	def test_posted_bills_leave_the_accounts_right(self):
		self.september()
		self.read_month("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.october()
		for b in frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["name", "connection", "payable_within_due", "ledger_entry"]):
			self.assertEqual(account.balance(b.connection), b.payable_within_due)
			e = frappe.db.get_value("Customer Ledger Entry", b.ledger_entry, ["seq", "voucher_no", "entry_type"], as_dict=True)
			self.assertEqual((e.voucher_no, e.entry_type), (b.name, "Bill"))
			seqs = frappe.get_all("Customer Ledger Entry", filters={"connection": b.connection}, pluck="seq", order_by="seq")
			self.assertEqual(seqs, list(range(1, len(seqs) + 1)))  # no gap, no repeat
		# the run can still be reversed, and a payment still posts after the bill
		self.pay_account(self.conns[4], 100, "2026-11-05")
		self.assertEqual(frappe.db.get_value("Customer Ledger Entry", {"connection": self.conns[4]}, "seq", order_by="seq desc"), 3)

	def test_a_recalculation_replaces_the_drafts(self):
		self.september()
		self.read_month("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.october(post=False)
		first = {b.connection: b.name for b in frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["name", "connection"])}
		lines = frappe.db.count("Bill Line", {"parent": ["in", list(first.values())]})
		billing.calculate_run(run.name)
		second = {b.connection: b.name for b in frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["name", "connection"])}
		# the same five bills with the same numbers (the series has no gap after a recalculation), and no line left behind or doubled
		self.assertEqual((len(first), second), (5, first))
		self.assertEqual(frappe.db.count("Bill Line", {"parent": ["in", list(first.values())]}), lines)
		# when other bills were numbered in between, the numbers move on instead
		frappe.db.sql("update `tabSeries` set `current` = `current` + 1 where name = %s", bulk._series("Utility Bill")[0])
		billing.calculate_run(run.name)
		third = set(frappe.get_all("Utility Bill", filters={"bill_run": run.name}, pluck="name"))
		self.assertEqual((len(third), third & set(first.values())), (5, set()))

	def test_a_held_account_is_left_out_and_reported(self):
		self.september()
		self.read_month("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		flag = frappe.db.get_value("Connection Flag Type", {"hold_billing": 1, "enabled": 1}) or frappe.get_doc(
			{"doctype": "Connection Flag Type", "flag_name": "TST Hold Billing", "hold_billing": 1, "enabled": 1}).insert().name
		c = frappe.get_doc("Connection", self.conns[2])
		c.append("connection_flags", {"flag": flag, "from_date": "2026-10-01"})
		c.save()
		run = self.october(post=False)
		self.assertEqual(run.bill_count, 4)
		self.assertEqual([p.connection for p in run.problems], [self.conns[2]])

	def test_numbers_are_taken_from_the_series_in_one_step(self):
		names = bulk.reserve_names("Utility Bill", 3)
		numbers = [int(n.rsplit("-", 1)[1]) for n in names]
		self.assertEqual(numbers, [numbers[0], numbers[0] + 1, numbers[0] + 2])
		self.assertEqual(len({n.rsplit("-", 1)[0] for n in names}), 1)
		self.assertEqual(int(bulk.reserve_names("Utility Bill", 1)[0].rsplit("-", 1)[1]), numbers[2] + 1)

	def test_a_site_that_reacts_to_bills_keeps_the_long_way(self):
		self.assertTrue(bulk.plain("Utility Bill", "Customer Ledger Entry"))
		frappe.get_doc({"doctype": "Webhook", "__newname": "TST bill hook", "webhook_doctype": "Utility Bill", "webhook_docevent": "on_submit", "request_url": "https://example.com/hook",
		                "request_method": "POST", "request_structure": "", "enabled": 1}).insert(ignore_permissions=True)
		self.assertFalse(bulk.plain("Utility Bill", "Customer Ledger Entry"))
