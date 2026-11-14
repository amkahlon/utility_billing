# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for period close and lock, year end, bill delivery, the run snapshot, tariff changes inside a reading period and exact
collected tax (increment 16; FS-RUN-10, 11, 12, 13, FS-ACC-10, FS-CFG-05, FS-BIL-02, FS-REC-17). House N 2308 (spec test case 1)."""

import json
from datetime import date
from decimal import Decimal

import frappe
from frappe.tests import UnitTestCase

from utility_billing.utility_billing import account, allocation, bill_engine as be, delivery, mis, periods, snapshots, split_engine, yearend
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	BILLING_MANAGER, BILLING_OFFICER, CASHIER, GENERAL_MANAGER, RECOVERY_OFFICER, approve,
)

D = Decimal


class UnitTestPeriodRules(UnitTestCase):
	def test_a_month_has_its_first_and_last_day(self):
		self.assertEqual(periods.bounds("2026-02-17"), (date(2026, 2, 1), date(2026, 2, 28)))
		self.assertEqual(periods.bounds("2028-02-17"), (date(2028, 2, 1), date(2028, 2, 29)))

	def test_tariff_days_split_the_reading_period_where_a_tariff_starts(self):
		t = [("old", date(2026, 1, 1), date(2026, 9, 20)), ("new", date(2026, 9, 21), None)]
		self.assertEqual(split_engine.tariff_days(date(2026, 9, 5), date(2026, 10, 5), t),
		                 [("old", date(2026, 9, 5), date(2026, 9, 20), 16), ("new", date(2026, 9, 21), date(2026, 10, 4), 14)])

	def test_one_tariff_over_the_whole_period_is_one_part(self):
		t = [("old", date(2026, 1, 1), date(2026, 8, 31)), ("new", date(2026, 9, 1), None)]
		parts = split_engine.tariff_days(date(2026, 9, 5), date(2026, 10, 5), t)
		self.assertEqual([(p[0], p[3]) for p in parts], [("new", 30)])

	def test_no_days_no_parts(self):
		self.assertEqual(split_engine.tariff_days(date(2026, 9, 5), date(2026, 9, 5), [("a", date(2026, 1, 1), None)]), [])

	def test_units_split_by_days_and_always_add_up(self):
		self.assertEqual(split_engine.split_units(264, [16, 14]), [D("140.80"), D("123.20")])
		shares = split_engine.split_units(100, [10, 10, 10])
		self.assertEqual((shares, sum(shares)), ([D("33.33"), D("33.33"), D("33.34")], D(100)))

	def test_a_split_bill_prices_each_share_on_its_own_tariff(self):
		energy = be.Charge("E", "Energy Charges", "Electricity", 10, be.NEAREST, be.PER_UNIT)
		service = be.Charge("S", "Service", "Society", 40, be.NEAREST, be.FIXED, D(1700))
		seg = lambda u, rate, frac, note: {"units": u, "tariff": {"method": "Flat rate", "flat_rate": rate}, "charges": [service],  # noqa: E731
		                                  "fraction": frac, "note": note}
		r = be.calculate(264, {}, energy, segments=[seg(D("140.8"), 70, D(16) / 30, "16 of 30 days at old"), seg(D("123.2"), 80, D(14) / 30, "14 of 30 days at new")])
		energy_lines = [l for l in r.lines if l.charge_type == "E"]
		self.assertEqual([(l.quantity, l.amount) for l in energy_lines], [(D("140.8"), D(9856)), (D("123.2"), D(9856))])
		self.assertIn("16 of 30 days at old", energy_lines[0].note)
		self.assertEqual(sum(l.amount for l in r.lines if l.charge_type == "S"), D(1700))
		self.assertEqual(r.current, D(9856 + 9856 + 907 + 793))

	def test_payments_cover_the_oldest_bill_first(self):
		self.assertEqual(allocation.fifo(1_500, [1_000, 1_000, 1_000]), [D(1_000), D(500), D(0)])
		self.assertEqual(allocation.fifo(5_000, [1_000, 1_000]), [D(1_000), D(1_000)])
		self.assertEqual(allocation.fifo(0, [1_000]), [D(0)])
		self.assertEqual(allocation.shares(1_500, [1_000, 1_000, 0]), [D(1), D("0.5"), D(0)])


class IntegrationTestPeriodControls(BillingCase):
	def setUp(self):
		super().setUp()
		self.sep = self.september_posted_with_arrears()  # the September bills are issued 01-10 and one payment is made on 10-10
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)

	def october(self, issue="2026-10-20"):
		return self.post(self.make_run("2026-10-01", issue))

	def period(self, month, **kw):
		return frappe.get_doc({"doctype": "Billing Period", "society": SOC, "period_start": month, **kw}).insert()

	def close(self, doc, user=BILLING_MANAGER):
		frappe.set_user(user)
		try:
			return periods.close(doc.name)
		finally:
			frappe.set_user("Administrator")

	def result(self, rows, check):
		return next(r["result"] for r in rows if r["check"] == check)

	# ---- period close (RUN-11)
	def test_a_period_is_a_calendar_month_and_one_per_society_and_month(self):
		p = self.period("2026-10-17")
		self.assertEqual((str(p.period_start), str(p.period_end), p.status), ("2026-10-01", "2026-10-31", "Open"))
		with self.assertRaises(frappe.ValidationError):
			self.period("2026-10-02")  # October exists
		with self.assertRaises(frappe.ValidationError):
			self.period("2099-01-01")  # not started

	def test_the_month_cannot_be_closed_before_it_is_over(self):
		p = self.period(date.today().replace(day=1))
		with self.assertRaises(frappe.ValidationError) as cm:
			self.close(p)
		self.assertIn("isn't over", str(cm.exception))

	def test_a_draft_bill_run_stops_the_close_and_the_checklist_says_why(self):
		self.make_run("2026-10-01", "2026-10-20")  # calculated, not approved
		p = self.period("2026-10-01")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.close(p)
		self.assertIn("Bill runs approved", str(cm.exception))
		p.reload()
		self.assertEqual(p.status, "Open")
		rows = {r["check"]: r["result"] for r in periods.preview(p.name)}
		self.assertEqual((rows["Bill runs approved"], rows["Readings resolved"], rows["Counters closed"]), ("Failed", "Passed", "Passed"))

	def test_closing_a_clear_month_locks_it(self):
		self.october()
		p = self.period("2026-10-01")
		out = self.close(p)
		p.reload()
		self.assertEqual((p.status, p.closed_by, out["status"]), ("Closed", BILLING_MANAGER, "Closed"))
		self.assertEqual([h.action for h in p.history], ["Closed"])
		self.assertEqual(self.result(out["checklist"], "Bill runs approved"), "Passed")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.pay(100, "2026-10-12")  # a payment dated in the closed month
		self.assertIn("closed", str(cm.exception).lower())
		with self.assertRaises(frappe.ValidationError):
			account.post(self.conn, "Fee", {"fees": 100}, "Fee Charge", "X", "2026-10-12", "late fee")
		self.pay(100, "2026-11-02")  # the open month still works
		self.assertTrue(periods.is_closed(SOC, "2026-10-15"))
		self.assertFalse(periods.is_closed(SOC, "2026-11-15"))

	def test_a_closed_months_run_cannot_be_reversed(self):
		run = self.october()
		self.close(self.period("2026-10-01"))
		run.cancellation_reason = "Trying to undo October"
		with self.assertRaises(frappe.ValidationError) as cm:
			run.cancel()
		self.assertIn("closed", str(cm.exception).lower())

	def test_a_run_dated_in_a_closed_month_cannot_be_approved(self):
		self.october()
		self.close(self.period("2026-10-01"))
		self.month_reading("2026-11-01", 4100)
		self.fpa("2026-11-01", 1.30)
		run = self.make_run("2026-11-01", "2026-10-28")  # issued in the closed month
		with self.assertRaises(Exception) as cm:
			self.post(run)
		self.assertIn("closed", str(cm.exception).lower())

	def test_a_surcharge_due_in_a_closed_month_is_dated_in_the_first_open_one(self):
		self.october()
		self.close(self.period("2026-10-01"))
		self.assertEqual(str(periods.next_open_date(SOC, "2026-10-31")), "2026-11-01")
		self.assertEqual(str(periods.next_open_date(SOC, "2026-11-05")), "2026-11-05")

	def test_earlier_months_must_be_closed_first(self):
		self.october()
		self.period("2026-09-01")  # open
		p = self.period("2026-10-01")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.close(p)
		self.assertIn("Close September 2026 first", str(cm.exception))

	def test_who_may_close_and_who_may_reopen(self):
		self.october()
		p = self.period("2026-10-01")
		with self.assertRaises(frappe.PermissionError):
			self.close(p, RECOVERY_OFFICER)
		self.close(p)
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			periods.reopen(p.name, "I would like it open again please")
		frappe.set_user(GENERAL_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			periods.reopen(p.name, "short")
		periods.reopen(p.name, "A payment was dated in the wrong month")
		frappe.set_user("Administrator")
		p.reload()
		self.assertEqual((p.status, [h.action for h in p.history]), ("Open", ["Closed", "Reopened"]))
		self.pay(100, "2026-10-12")  # open again
		with self.assertRaises(frappe.ValidationError):
			p.delete()  # a month that was closed once keeps its history

	def test_only_the_latest_closed_month_can_be_reopened(self):
		self.october()
		sep, octo = self.period("2026-09-01"), self.period("2026-10-01")
		self.close(sep)
		self.close(octo)
		frappe.set_user(GENERAL_MANAGER)
		with self.assertRaises(frappe.ValidationError) as cm:
			periods.reopen(sep.name, "Reopen September to fix something")
		self.assertIn("October 2026 is closed too", str(cm.exception))
		periods.reopen(octo.name, "Reopen October first to fix something")
		periods.reopen(sep.name, "Now September can be reopened as well")
		frappe.set_user("Administrator")

	# ---- year end (RUN-12)
	def test_the_year_cannot_close_while_a_month_is_open(self):
		self.october()
		self.close(self.period("2026-10-01"))
		yc = frappe.get_doc({"doctype": "Year End Close", "society": SOC, "year_start": "2026-09-01", "year_end": "2026-10-31"}).insert()
		with self.assertRaises(frappe.ValidationError) as cm:
			yearend.close(yc.name)
		self.assertIn("Sep 2026", str(cm.exception))

	def test_closing_the_year_fixes_the_balances_and_the_revenue(self):
		self.october()
		for m in ("2026-09-01", "2026-10-01"):
			self.close(self.period(m))
		yc = frappe.get_doc({"doctype": "Year End Close", "society": SOC, "year_start": "2026-09-01", "year_end": "2026-10-31"}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Year End Close", "society": SOC, "year_start": "2026-10-01", "year_end": "2026-12-31"}).insert()  # overlaps
		yearend.prepare(yc.name)
		yearend.close(yc.name)
		yc.reload()
		self.assertEqual(yc.status, "Closed")
		owed = account.balance(self.conn)
		self.assertEqual((round(yc.closing_balance, 2), yc.accounts), (round(owed, 2), 1))
		moves = yc.opening_balance + yc.total_billed + yc.total_surcharge - yc.total_collected + yc.total_adjusted
		self.assertAlmostEqual(moves, yc.closing_balance, places=2)
		(row,) = yc.balances
		self.assertEqual((row.connection, round(row.total, 2)), (self.conn, round(owed, 2)))
		self.assertEqual(round(sum(r.amount for r in yc.revenue if r.charge_group == "Electricity" or r.label), 2) > 0, True)
		self.assertAlmostEqual(sum(r.amount for r in yc.revenue), yc.total_billed, places=2)
		with self.assertRaises(frappe.ValidationError):
			yc.delete()
		with self.assertRaises(frappe.ValidationError):
			yearend.close(yc.name)  # already closed
		frappe.set_user(GENERAL_MANAGER)
		with self.assertRaises(frappe.ValidationError) as cm:
			periods.reopen(frappe.db.get_value("Billing Period", {"society": SOC, "period_start": "2026-10-01"}), "Reopen October after the year end")
		frappe.set_user("Administrator")
		self.assertIn("year was closed", str(cm.exception))

	# ---- run snapshot (RUN-13)
	def test_posting_a_run_keeps_a_snapshot_and_restoring_reverses_it(self):
		before = {k: float(v) for k, v in account.balances(self.conn).items()}
		run = self.october()
		(snap_name,) = frappe.get_all("Run Snapshot", filters={"bill_run": run.name}, pluck="name")
		snap = frappe.get_doc("Run Snapshot", snap_name)
		self.assertEqual((snap.status, snap.bill_count, snap.accounts, snap.society), ("Kept", 1, 1, SOC))
		data = json.loads(snap.data)
		self.assertEqual({k: round(v, 2) for k, v in data["balances"][self.conn].items()}, {k: round(v, 2) for k, v in before.items()})
		self.assertEqual(data["bills"][0]["connection"], self.conn)
		frappe.set_user(RECOVERY_OFFICER)
		with self.assertRaises(frappe.PermissionError):
			snapshots.restore(snap_name, "Not allowed")
		frappe.set_user(BILLING_MANAGER)
		out = snapshots.restore(snap_name, "Wrong readings were used for October")
		frappe.set_user("Administrator")
		self.assertEqual(out["different"], [])
		self.assertEqual({k: round(float(v), 2) for k, v in account.balances(self.conn).items()}, {k: round(v, 2) for k, v in before.items()})
		snap.reload()
		self.assertEqual(snap.status, "Restored")
		self.assertIn("back to their balance", snap.restore_result)
		self.assertEqual(frappe.db.get_value("Bill Run", run.name, "docstatus"), 2)
		with self.assertRaises(frappe.ValidationError):
			snapshots.restore(snap_name, "Again")

	def test_a_restore_is_refused_when_money_came_in_after_the_bills(self):
		run = self.october()
		snap = frappe.get_all("Run Snapshot", filters={"bill_run": run.name}, pluck="name")[0]
		self.pay(100, "2026-11-02")
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError) as cm:
			snapshots.restore(snap, "Trying after a payment")
		frappe.set_user("Administrator")
		self.assertIn("Reverse it first", str(cm.exception))

	# ---- bill delivery (RUN-10)
	def test_delivery_is_tracked_with_a_history(self):
		run = self.october()
		bill = self.bill(run)
		self.assertFalse(bill.delivery_status)
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError):
			delivery.mark(run.name, "Handed to Distributor")  # a distributor is named
		self.assertEqual(delivery.mark(run.name, "Handed to Distributor", distributor="Ali Khan")["changed"], 1)
		self.assertEqual(delivery.mark(run.name, "Handed to Distributor", distributor="Ali Khan")["changed"], 0)  # nothing new
		delivery.mark(run.name, "Delivered", remarks="Left with the guard")
		delivery.mark(run.name, "Printed")  # a reprint doesn't undo delivery
		frappe.set_user("Administrator")
		bill.reload()
		self.assertEqual((bill.delivery_status, bill.distributor), ("Delivered", "Ali Khan"))
		self.assertEqual([(e.status, e.distributor) for e in bill.delivery_log], [("Handed to Distributor", "Ali Khan"), ("Delivered", None)])
		self.assertEqual(delivery.summary(run.name), {"total": 1, "by_status": {"Delivered": 1}})

	def test_only_billing_staff_record_delivery_and_a_block_or_route_can_be_chosen(self):
		run = self.october()
		frappe.set_user(RECOVERY_OFFICER)
		with self.assertRaises(frappe.PermissionError):
			delivery.mark(run.name, "Delivered")
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			delivery.mark(run.name, "Delivered")
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError):
			delivery.mark(run.name, "Delivered", block="Z9")  # no such block
		frappe.set_user("Administrator")

	def test_a_message_sent_marks_the_bill_unless_it_is_further_along(self):
		run = self.october()
		bill = self.bill(run)
		delivery.record([bill.name], delivery.MESSAGE, remarks="SMS", keep_stronger=True)
		self.assertEqual(frappe.db.get_value("Utility Bill", bill.name, "delivery_status"), "Sent by Message")
		delivery.record([bill.name], delivery.DELIVERED)
		delivery.record([bill.name], delivery.MESSAGE, keep_stronger=True)
		self.assertEqual(frappe.db.get_value("Utility Bill", bill.name, "delivery_status"), "Delivered")

	def test_the_delivery_report_counts_by_block_and_route(self):
		run = self.october()
		delivery.mark(run.name, "Delivered")
		cols, data, *_x = mis.run("bill_delivery_status", {"society": SOC, "bill_run": run.name})
		total = data[-1]
		self.assertEqual((total["bills"], total["delivered"], total["pending"], total["not_recorded"]), (1, 1, 0, 0))
		_c, data, *_x = mis.run("bill_delivery_status", {"society": SOC, "bill_run": run.name, "status": "Printed"})
		self.assertEqual(data, [])

	def test_the_period_report_lists_months_and_years(self):
		self.october()
		p = self.period("2026-10-01")
		self.close(p)
		_c, data, msg, *_x = mis.run("period_close_status", {"society": SOC})
		self.assertEqual([(r["status"], r["failed"], r["reopened"]) for r in data], [("Closed", 0, 0)])
		self.assertIsNone(msg)

	# ---- a tariff changing in the middle of the reading period (CFG-05, BIL-02)
	def new_tariff(self, effective_from, rate=80):
		t = frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": self.cat,
		                    "effective_from": effective_from, "pricing_method": "Flat rate", "flat_rate": rate,
		                    "charges": [{"charge_type": "TST Service", "rate": 1700}, {"charge_type": "TST Aquifer", "rate": 1000}]}).insert()
		approve(t)
		t.reload()
		return t

	def test_a_tariff_starting_inside_the_reading_period_splits_the_bill_by_days(self):
		new = self.new_tariff("2026-09-21")  # the October reading period is 05-09 to 05-10 (30 days): 16 under 70, 14 under 80
		bill = self.bill(self.october())
		energy = [l for l in bill.lines if l.label == "Energy Charges"]
		self.assertEqual([(round(l.quantity, 2), l.amount) for l in energy], [(140.8, 9856), (123.2, 9856)])
		self.assertIn("16 of 30 days", energy[0].note)
		self.assertIn("14 of 30 days", energy[1].note)
		self.assertEqual(round(sum(l.quantity for l in energy), 2), bill.units)
		fixed = {n: sum(l.amount for l in bill.lines if l.label == n) for n in ("Service Charges", "Aquifer Charges")}
		self.assertEqual(fixed, {"Service Charges": 1700, "Aquifer Charges": 1000})  # the days add back to the month
		self.assertEqual((bill.current_amount, bill.tariff), (9856 + 9856 + 1700 + 1000 + 1080, new.name))

	def test_a_tariff_starting_after_the_reading_period_changes_nothing(self):
		self.new_tariff("2026-10-20")
		bill = self.bill(self.october())
		self.assertEqual(len([l for l in bill.lines if l.label == "Energy Charges"]), 1)
		self.assertEqual(bill.current_amount, 22_260)  # spec test case 1

	def test_the_split_can_be_switched_off(self):
		frappe.db.set_value("Society", SOC, "tariff_split_method", "Off")
		self.new_tariff("2026-09-21")
		bill = self.bill(self.october())
		self.assertEqual(len([l for l in bill.lines if l.label == "Energy Charges"]), 1)
		self.assertEqual(bill.current_amount, 264 * 80 + 1700 + 1000 + 1080)  # the tariff of the billing month for the whole bill
