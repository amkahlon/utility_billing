# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for adjustments, bills and bill runs (increment 5), including spec test case 1."""

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing.install import create_default_exception_codes, create_default_utilities
from utility_billing.utility_billing import billing, readings
from utility_billing.utility_billing.approval import set_rejection_reason
from utility_billing.utility_billing.testing import (
	BILLING_MANAGER,
	BILLING_OFFICER,
	GENERAL_MANAGER,
	act,
	approve,
	make_users,
)

SOC = "TST-BIL"


def ensure(doctype, name, data):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **data}).insert()
	return name


class BillingCase(IntegrationTestCase):
	"""Shared fixtures (spec test case 1: house N 2308); no tests of its own, so other modules can reuse it."""

	def setUp(self):
		create_default_utilities()
		create_default_exception_codes()
		make_users()
		for name, group, basis, order in (("TST Energy", "Electricity", "Per Unit", 10), ("TST FPA", "Electricity", "Per Unit", 20),
		                                  ("TST Service", "Society", "Fixed", 40), ("TST Aquifer", "Society", "Fixed", 50)):
			ensure("Charge Type", name, {"charge_name": name, "charge_group": group, "calculation_basis": basis,
			                             "bill_order": order, "bill_label": name.replace("TST ", "") + " Charges"})
		ensure("Society", SOC, {"society_name": "Billing Test Society", "short_name": SOC, "energy_charge_type": "TST Energy",
		                        "charges_fpa": 1, "surcharge_rate": 8, "days_to_due_date": 14})
		self.cat = ensure("Consumer Category", "Electricity-TST Billing", {"category_name": "TST Billing", "utility": "Electricity"})
		if not frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": self.cat, "docstatus": 1}):
			t = frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": self.cat,
			                    "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": 70,
			                    "charges": [{"charge_type": "TST Service", "rate": 1700}, {"charge_type": "TST Aquifer", "rate": 1000}]}).insert()
			approve(t)
		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "House N 2308 Owner"}).insert().name
		self.prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2308"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M1", "society": SOC, "utility": "Electricity"}).insert().name
		self.conn = frappe.get_doc({
			"doctype": "Connection", "society": SOC, "property": self.prop, "consumer": consumer, "utility": "Electricity",
			"consumer_category": self.cat, "status": "Active",
			"meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 1999}],
		}).insert().name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ----- helpers -----
	def month_reading(self, month, present, close=True):
		cycle = f"{SOC}-Electricity-{month[:7]}"
		if not frappe.db.exists("Reading Cycle", cycle):
			frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading, r.reading_date = present, f"{month[:8]}05"
		r.save()
		if r.status == "Flagged":
			readings.approve_reading(r.name, "Confirmed with the consumer")
		if close:
			readings.close_cycle(cycle)
		return cycle

	def fpa(self, month, rate, approve_it=True, exempt=()):
		frappe.set_user(BILLING_MANAGER)
		adj = frappe.get_doc({"doctype": "Tariff Adjustment", "adjustment_type": "FPA", "society": SOC, "utility": "Electricity",
		                      "billing_month": month, "rate_per_unit": rate, "charge_type": "TST FPA",
		                      "exempt_categories": [{"consumer_category": c} for c in exempt]}).insert()
		frappe.set_user("Administrator")
		if approve_it:
			act(adj, "Submit for Approval", BILLING_MANAGER)
			act(adj, "Approve", GENERAL_MANAGER)
			adj.reload()
		return adj

	def make_run(self, month, issue, calculate=True, exclusions=()):
		frappe.set_user(BILLING_OFFICER)
		run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": month,
		                      "issue_date": issue, "exclusions": [{"connection": c, "reason": r} for c, r in exclusions]}).insert()
		if calculate:
			billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		run.reload()
		return run

	def post(self, run):
		act(run, "Submit for Approval", BILLING_OFFICER)
		act(run, "Approve", BILLING_MANAGER)
		run.reload()
		return run

	def bill(self, run):
		return frappe.get_doc("Utility Bill", {"bill_run": run.name, "connection": self.conn})

	def september_posted_with_arrears(self):
		"""Aug 831 units, Sep 811 units; September bill posted and paid on 10-10-2026 except Rs 1,104."""
		self.month_reading("2026-08-01", 2830)
		self.month_reading("2026-09-01", 3641)
		self.fpa("2026-09-01", 0)
		sep = self.post(self.make_run("2026-09-01", "2026-10-01"))
		self.pay(self.bill(sep).payable_within_due - 1104, "2026-10-10")  # Rs 1,104 left unpaid
		return sep

	def pay(self, amount, date, txn=None, mode="Bank Transfer"):
		"""A bank/channel payment recorded by the Administrator (System Manager)."""
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Bank / Channel",
		                    "payment_date": date, "amount": amount, "mode": mode,
		                    "channel_txn_id": txn or frappe.generate_hash(length=12)}).insert()
		r.submit()
		return r


class IntegrationTestBilling(BillingCase):
	def test_case_1_october_bill_end_to_end(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		adj = self.fpa("2026-10-01", 1.30)
		self.assertEqual(str(adj.reference_month), "2026-08-01")  # billing month - 2
		run = self.make_run("2026-10-01", "2026-11-01")
		self.assertEqual((run.calculation_status, run.bill_count, len(run.problems)), ("Calculated", 1, 0))
		b = self.bill(run)
		self.assertEqual([(l.label, l.amount) for l in b.lines], [
			("Energy Charges", 18480), ("FPA Charges", 1080), ("Service Charges", 1700), ("Aquifer Charges", 1000)])
		self.assertEqual((b.units, b.current_amount, b.arrears, b.payable_within_due), (264, 22260, 1104, 23364))
		self.assertEqual((b.surcharge_amount, b.payable_after_due), (1781, 25145))
		self.assertIn("August 2026 units 831", b.lines[1].note)
		self.assertEqual(str(b.due_date), "2026-11-15")
		self.assertEqual(b.docstatus, 0)

		run = self.post(run)
		b.reload()
		self.assertEqual((run.workflow_state, b.docstatus, b.status, b.outstanding_amount), ("Approved", 1, "Posted", 23364))
		self.assertEqual([r.action for r in run.approval_log], ["Submit for Approval", "Approve"])

	def test_summary_compares_previous_month(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		sep_current = frappe.db.get_value("Utility Bill", {"connection": self.conn, "billing_month": "2026-09-01"}, "current_amount")
		self.assertEqual((run.total_current, run.previous_month_current), (22260, sep_current))
		energy = next(r for r in run.summary if r.item == "Energy Charges")
		self.assertEqual((energy.amount, energy.previous_amount), (18480, 811 * 70))

	def test_reading_cycle_must_be_closed(self):
		self.month_reading("2026-10-01", 3905, close=False)
		self.fpa("2026-10-01", 1.30)
		with self.assertRaises(frappe.ValidationError):
			self.make_run("2026-10-01", "2026-11-01")

	def test_problems_listed_not_hidden(self):
		from unittest.mock import patch  # noqa: PLC0415

		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		with patch.object(billing, "build_bill", side_effect=billing.BillProblem("Tariff TST has no amount for plot size 10 Marla.")):
			run = self.make_run("2026-10-01", "2026-11-01")
		self.assertEqual(run.bill_count, 0)
		self.assertIn("no amount for plot size", run.problems[0].problem)
		with self.assertRaises(frappe.ValidationError):
			act(run, "Submit for Approval", BILLING_OFFICER)  # nothing to approve

	def test_missing_fpa_refused_once_for_the_run(self):
		"""Increment 52: a missing FPA stops the run with one message, not one problem per account (21,446 on the demo site)."""
		self.month_reading("2026-10-01", 3905)
		run = self.make_run("2026-10-01", "2026-11-01", calculate=False)
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError) as e:
			billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		self.assertIn("No approved FPA for October 2026", str(e.exception))
		self.assertIn("a rate of 0", str(e.exception))
		self.assertNotEqual(frappe.db.get_value("Bill Run", run.name, "calculation_status"), "Calculating")

	def test_calculation_progress_and_no_history_record(self):
		"""Increment 52: the run says how far it is; its save writes no change-history record (that one grew past 16 MB)."""
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01", calculate=False)
		before = frappe.db.count("Version", {"ref_doctype": "Bill Run", "docname": run.name})
		frappe.set_user(BILLING_OFFICER)
		billing.calculate_run(run.name)
		p = billing.calculation_progress(run.name)
		frappe.set_user("Administrator")
		self.assertEqual((p["state"], p["done"], p["total"]), ("Done", 1, 1))
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": "Bill Run", "docname": run.name}), before)

	def test_calculation_already_running_or_stopped(self):
		"""Increment 52: a second Calculate while one runs is refused; a run left at Calculating with no news is shown as stopped."""
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01", calculate=False)
		billing._tell(run.name, "Running", 500, 21446)
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError) as e:
			billing.calculate_run(run.name)
		self.assertIn("being calculated already: 500 of 21446", str(e.exception))
		frappe.set_user("Administrator")
		frappe.cache.delete_value(billing._progress_key(run.name))
		frappe.db.set_value("Bill Run", run.name, "calculation_status", "Calculating")
		frappe.set_user(BILLING_OFFICER)
		p = billing.calculation_progress(run.name)
		frappe.set_user("Administrator")
		self.assertEqual(p["state"], "Failed")
		self.assertIn("Calculate again", p["error"])
		self.assertEqual(frappe.db.get_value("Bill Run", run.name, "calculation_status"), "Failed")

	def test_fpa_exempt_category(self):
		self.month_reading("2026-08-01", 2830)
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30, exempt=[self.cat])
		b = self.bill(self.make_run("2026-10-01", "2026-11-01"))
		fpa = next(l for l in b.lines if l.label == "FPA Charges")
		self.assertEqual(fpa.amount, 0)
		self.assertIn("Exempt", fpa.note)

	def test_fpa_without_reference_reading(self):
		self.month_reading("2026-10-01", 3905)  # no August reading
		self.fpa("2026-10-01", 1.30)
		b = self.bill(self.make_run("2026-10-01", "2026-11-01"))
		fpa = next(l for l in b.lines if l.label == "FPA Charges")
		self.assertEqual((fpa.amount, fpa.note), (0, "No reading for August 2026"))

	def test_fpa_on_a_month_from_the_previous_system(self):
		"""Increment 57: the demo society's November FPA (on September) came out 0 on every bill: September was billed by the previous
		system, whose units came over as history (Legacy Bill), and only readings were looked at. A reading still comes first."""
		frappe.get_doc({"doctype": "Legacy Bill", "connection": self.conn, "billing_month": "2026-08-01", "units": 700, "bill_amount": 1}).insert()
		self.month_reading("2026-10-01", 3905)  # no August reading here
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		fpa = next(l for l in self.bill(run).lines if l.label == "FPA Charges")
		self.assertEqual((fpa.amount, fpa.note), (910, "August 2026 (previous system) units 700 × 1.3"))
		self.assertIsNone(readings.legacy_units(self.conn, "2026-07-01"))  # a month its history doesn't have

	def test_adjustment_duplicate_and_maker_checker(self):
		adj = self.fpa("2026-10-01", 1.30, approve_it=False)
		with self.assertRaises(frappe.ValidationError):
			self.fpa("2026-10-01", 1.40, approve_it=False)
		act(adj, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			act(adj, "Approve", BILLING_MANAGER)  # Billing Manager has no Approve action
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason("Tariff Adjustment", adj.name, "Rate must follow NEPRA notification")
		frappe.set_user("Administrator")
		act(adj, "Reject", GENERAL_MANAGER)
		adj.reload()
		self.assertEqual(adj.workflow_state, "Rejected")

	def test_run_maker_cannot_approve_and_exclusions(self):
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01", exclusions=[(self.conn, "Meter under dispute")])
		self.assertEqual(run.bill_count, 0)
		# Removing the exclusion makes the calculation stale until recalculated.
		frappe.set_user(BILLING_OFFICER)
		run = frappe.get_doc("Bill Run", run.name)
		run.exclusions = []
		run.save()
		self.assertEqual(run.calculation_status, "Not calculated")
		with self.assertRaises(frappe.ValidationError):
			frappe.set_user("Administrator")
			act(run, "Submit for Approval", BILLING_OFFICER)
		frappe.set_user(BILLING_OFFICER)
		billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		run.reload()
		self.assertEqual(run.bill_count, 1)
		act(run, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			act(run, "Approve", BILLING_MANAGER)  # submitted it, so can't approve it

	def test_no_double_billing(self):
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.post(self.make_run("2026-10-01", "2026-11-01"))
		with self.assertRaises(frappe.ValidationError):
			self.make_run("2026-10-01", "2026-11-02", calculate=False)  # one run per month

	def test_a_supplementary_run_bills_only_what_the_main_run_left_out(self):
		cons = frappe.db.get_value("Connection", self.conn, "consumer")
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2310"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M3", "society": SOC, "utility": "Electricity"}).insert().name
		other = frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": cons, "utility": "Electricity",
		                        "consumer_category": self.cat, "status": "Active",
		                        "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 100}]}).insert().name
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": other})
		r.present_reading, r.reading_date = 400, "2026-10-05"
		r.save()
		if r.status == "Flagged":
			readings.approve_reading(r.name, "Checked")
		readings.close_cycle(cycle)
		self.fpa("2026-10-01", 1.30)
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError):  # the main run comes first
			frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01",
			                "issue_date": "2026-11-01", "supplementary": 1}).insert()
		frappe.set_user("Administrator")
		self.post(self.make_run("2026-10-01", "2026-11-01", exclusions=[(self.conn, "Meter under dispute")]))
		frappe.set_user(BILLING_OFFICER)
		supp = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01",
		                       "issue_date": "2026-11-05", "supplementary": 1}).insert()
		billing.calculate_run(supp.name)
		frappe.set_user("Administrator")
		supp.reload()
		self.assertEqual((supp.bill_count, len(supp.problems)), (1, 0))
		self.post(supp)
		frappe.set_user(BILLING_OFFICER)
		again = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01",
		                        "issue_date": "2026-11-06", "supplementary": 1}).insert()
		billing.calculate_run(again.name)
		frappe.set_user("Administrator")
		again.reload()
		self.assertEqual((again.bill_count, len(again.problems)), (0, 0))  # nothing left to bill, nobody billed twice

	def test_bills_only_through_runs_and_cancel(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Utility Bill", "connection": self.conn, "billing_month": "2026-10-01"}).insert()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		b = self.bill(run)
		b.flags.from_bill_run = False
		with self.assertRaises(frappe.ValidationError):
			b.cancel()  # bills are cancelled only through their run
		frappe.db.set_value("Bill Run", run.name, "cancellation_reason", "Run reversed: wrong readings")  # FS-RUN-06 needs a reason
		act(run, "Cancel", BILLING_MANAGER)
		b.reload()
		self.assertEqual((b.docstatus, b.status, b.outstanding_amount), (2, "Cancelled", 0))
