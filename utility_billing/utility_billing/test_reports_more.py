# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for the remaining reports (increment 20; FS-RPT-03, 11, 12, 16, 22, 25, 28, 33, 36, 38, 43, 44). House N 2308 (spec test case 1)."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.utils import nowdate

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, mis, plans, servicereq
from utility_billing.utility_billing.doctype.installment_plan import installment_plan as plan_module
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, act


@contextmanager
def today(date="2026-11-10"):
	with patch.object(plan_module, "nowdate", return_value=date), patch.object(plans, "nowdate", return_value=date):
		yield


def run(key, **f):
	return mis.run(key, {"society": SOC, **f})


class IntegrationTestReportsMore(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		servicereq.ensure_types(SOC)
		self.sep = self.september_posted_with_arrears()  # owes 1,104

	def request(self, code, **kw):
		return frappe.get_doc({"doctype": "Service Request", "request_type": f"{SOC}-{code}", "connection": self.conn, **kw}).insert()

	def finish(self, sr):
		while sr.status in ("Open", "In Progress"):
			sr.reload()
			step = next(s for s in sr.steps if s.idx == sr.current_step)
			frappe.set_user({"Billing Manager": BILLING_MANAGER}.get(step.role, "Administrator"))
			try:
				servicereq.advance(sr.name, step.idx)
			finally:
				frappe.set_user("Administrator")
			sr.reload()
		return sr

	def test_installment_plans(self):
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.post(self.make_run("2026-10-01", "2026-11-01"))  # the unpaid 1,104 becomes arrears
		with today():
			p = frappe.get_doc({"doctype": "Installment Plan", "connection": self.conn, "amount_to_split": 1000, "down_payment": 200, "installments": 2,
			                    "first_month": "2026-11-01", "remarks": "Agreed at the office"}).insert()
			act(p, "Submit for Approval", BILLING_OFFICER)
			self.pay(200, "2026-11-10", txn="DP-R")
			act(p, "Approve", BILLING_MANAGER)
		_c, data, _m, _ch, summary = run("installment_plans")
		self.assertEqual((data[0]["name"], data[0]["plan_status"], data[0]["to_come"]), (p.name, "Active", 800))
		self.assertEqual(summary[0]["value"], 1)

	def test_movement_disconnections_and_no_dues(self):
		self.finish(self.request("DISC", disconnection_reason="Safety"))
		_c, data, _m, _ch, summary = run("consumer_movement", from_date="2026-01-01", to_date="2030-12-31")
		self.assertIn({"movement": "Disconnection", "count": 1}, data)
		self.assertEqual(summary[1]["value"], 1)
		_c, data, *_x = run("disconnections_register", from_date="2026-01-01", to_date="2030-12-31")
		self.assertEqual((data[0]["kind"], data[0]["status"], data[0]["owes"]), ("Disconnection", "Completed", 1104))
		self.pay(1104, nowdate())
		nd = self.finish(self.request("NODUES"))
		_c, data, *_x = run("nodues_certificates", from_date="2026-01-01", to_date="2030-12-31")
		self.assertEqual((data[0]["certificate_no"], data[0]["connection"]), (nd.certificate_no, self.conn))

	def test_detection_bills_are_listed(self):
		sr = self.request("DETECT", assessed_load_kw=2, hours_per_day=6, assessed_days=10, rate_per_unit=40)
		_c, data, *_x = run("detection_abnormal", month=nowdate())
		self.assertEqual((data[0]["kind"], data[0]["units"], data[0]["amount"]), ("Detection bill", 120, 4800))
		self.assertIn(sr.name, data[0]["note"])

	def test_channel_fees(self):
		soc = frappe.get_doc("Society", SOC)
		soc.append("channel_fees", {"mode": "Bank Transfer", "fee_percent": 1, "fixed_fee": 10, "cap": 50})
		soc.save()
		_c, data, *_x = run("channel_fees", from_date="2026-10-01", to_date="2026-10-31")
		row = data[0]
		self.assertEqual((row["payments"], row["fees"]), (1, 50))  # 1% of the payment + 10, at most 50
		self.assertEqual(row["net"], round(row["collected"] - 50, 2))

	def test_revenue_against_target(self):
		frappe.get_doc({"doctype": "Revenue Target", "society": SOC, "target_year": 2026,
		                "months": [{"month": "2026-09-01", "billed": 20000, "collected": 10000}]}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Revenue Target", "society": SOC, "target_year": 2027, "months": [{"month": "2026-09-01", "billed": 1}]}).insert()
		_c, data, *_x = run("revenue_vs_target", year=2026)
		sep = next(r for r in data if r["month"] == "Sep 2026")
		bill = self.bill(self.sep)
		self.assertEqual((sep["budget_billed"], sep["billed"], sep["billed_diff"]), (20000, bill.current_amount, round(bill.current_amount - 20000, 2)))

	def test_analytics_run_for_every_view(self):
		for show in ("Theft Suspicion", "Seasonality", "Revenue Forecast"):
			cols, data, *_x = run("consumption_analytics", month="2026-09-01", show=show)
			self.assertTrue(cols, show)
		_c, data, *_x = run("consumption_analytics", month="2026-09-01", show="Seasonality")
		self.assertEqual((data[0]["month"], data[0]["index"]), ("September", 100))
		_c, data, *_x = run("consumption_analytics", month="2026-09-01", show="Revenue Forecast")
		self.assertEqual(len(data), 3)

	def test_the_maximum_demand_read_with_the_meter_is_the_connected_load(self):
		frappe.db.set_value("Connection", self.conn, "sanctioned_load_kw", 3)
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.mdi_kw = -1
		with self.assertRaises(frappe.ValidationError):
			r.save()
		r = frappe.get_doc("Meter Reading", r.name)
		r.mdi_kw = 4.5
		r.save()
		self.assertIn("Maximum demand 4.5 kW is above the sanctioned load of 3.0 kW", r.checks)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, ["last_mdi_kw", "last_mdi_date"]), (4.5, r.reading_date))
		_c, data, *_x = run("consumer_census")
		total = data[-1]
		self.assertEqual((total["mdi_kw"], total["mdi_read"], total["over_load"]), (4.5, 1, 1))

	def test_meter_under_test_and_transformer_measured_loading(self):
		self.request("TEST", test_error_percent=3)
		_c, data, _m, _ch, summary = run("meter_register")
		self.assertEqual(next(r for r in data if r["connection"] == self.conn)["under_test"], 1)
		self.assertEqual(next(s for s in summary if s["label"] == "Under test")["value"], 1)
		feeder = frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "F9", "feeder_name": "Feeder 9"}).insert().name
		tx = frappe.get_doc({"doctype": "Transformer", "society": SOC, "transformer_code": "T9", "feeder": feeder, "kva_rating": 100}).insert().name
		frappe.get_doc({"doctype": "DISCO Bill", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01", "units_kwh": 10000,
		                "energy_amount": 500000, "inputs": [{"transformer": tx, "input_kwh": 7200}]}).insert()
		_c, data, *_x = run("transformer_loading", month="2026-10-01")
		row = next(r for r in data if r["transformer"] == tx)
		self.assertEqual((row["avg_kw"], row["measured_loading"]), (9.68, 10.8))  # 7,200 kWh ÷ 744 h; ÷ (100 kVA × 0.9)
