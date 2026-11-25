# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Server side of the screens redesigned after the mock-ups (2026-11-16): Consumer 360, the tariff editor, defaulters and recovery,
the cash counter, reading review and the bill run wizard."""

import json

import frappe
from frappe.utils import flt

from utility_billing.utility_billing import (
	billing, billrun_wizard, cashdesk, consumer360, readings, reading_review, recovery, tariff_studio,
)
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_OFFICER, CASHIER


class IntegrationTestScreens(BillingCase):
	def october(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	# ---- Consumer 360
	def test_search_gives_one_row_per_account_with_balance_and_what_matched(self):
		self.september_posted_with_arrears()
		rows = [x for x in consumer360.search("N 2308", limit=200) if x.account == self.conn]  # QA data has other N 2308 houses
		self.assertEqual(len(rows), 1)
		r = rows[0]
		self.assertEqual((r.account, r.house, r.matched, r.utility), (self.conn, "N 2308", "house", "Electricity"))
		self.assertEqual(round(r.balance), 1104)
		self.assertEqual(consumer360.search("House N 2308 Owner")[0].matched, "name")
		self.assertEqual(consumer360.search(frappe.db.get_value("Connection", self.conn, "reference_no"))[0].matched, "reference")
		self.assertEqual(consumer360.search("x"), [])

	def test_view_of_an_account_has_its_overview(self):
		self.october()
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		self.assertEqual(consumer360.consumer_of(self.conn)["consumer"], consumer)
		v = consumer360.view(consumer, self.conn)
		self.assertEqual(v["selected"], self.conn)
		o = v["overview"]
		self.assertGreater(o["payable"]["within_due"], 0)
		self.assertEqual(len(o["consumption"]), 12)
		self.assertEqual([m["units"] for m in o["consumption"] if m["units"] is not None][-3:], [831, 811, 264])

	# ---- tariff editor
	def test_tariff_editor_new_version_save_and_impact(self):
		self.october()
		old = frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": self.cat, "docstatus": 1})
		d = tariff_studio.load(old)
		self.assertEqual(d["state"], "Approved")
		new = tariff_studio.new_version(old)
		self.assertEqual(frappe.db.get_value("Tariff", new, "effective_from").day, 1)
		saved = tariff_studio.save(new, json.dumps({"flat_rate": 77}))
		self.assertEqual(flt(frappe.db.get_value("Tariff", new, "flat_rate")), 77)
		self.assertEqual(saved["name"], new)
		imp = tariff_studio.impact(new)
		self.assertEqual(imp["bills"], 1)
		self.assertEqual(round(imp["example"]["difference"]), 264 * 7)  # 264 units at Rs 7 more
		self.assertEqual(imp["bands"][0]["band"], "201–400")
		with self.assertRaises(frappe.ValidationError):
			tariff_studio.save(old, json.dumps({"flat_rate": 1}))

	# ---- defaulters and recovery
	def test_stage_labels(self):
		self.assertEqual(recovery.stage_label({"promise": "Broken"})[1], "red")
		self.assertEqual(recovery.stage_label({"hold": "Installment plan active"})[0], "Installment plan")
		self.assertEqual(recovery.stage_label({"next_stage": "Disconnection Order"})[0], "For disconnection")
		self.assertEqual(recovery.stage_label({"last_stage": "Reminder"})[0], "Reminder sent")

	def test_disconnection_requests_from_the_selection_bar(self):
		self.september_posted_with_arrears()
		out = recovery.disconnection_requests(SOC, json.dumps([self.conn]))
		self.assertEqual(len(out["made"]), 1)
		self.assertEqual(frappe.db.get_value("Service Request", out["made"][0], "connection"), self.conn)
		again = recovery.disconnection_requests(SOC, [self.conn])
		self.assertEqual((again["made"], again["skipped"]), ([], [self.conn]))

	# ---- cash counter
	def test_cash_counter_lookup_and_post(self):
		run = self.october()
		if not frappe.db.exists("Cash Counter", "TB1"):
			frappe.get_doc({"doctype": "Cash Counter", "counter_code": "TB1", "counter_name": "Test counter", "society": SOC}).insert()
		ref = frappe.db.get_value("Connection", self.conn, "reference_no")
		frappe.set_user(CASHIER)
		self.assertFalse(cashdesk.state()["open"])
		frappe.get_doc({"doctype": "Cashier Day", "cash_counter": "TB1", "opening_float": 1000}).insert()
		found = cashdesk.lookup(f"https://pay.example/bill/{ref}")
		self.assertEqual((found["reference"], found["property_no"]), (ref, "N 2308"))
		due = flt(found["amount_due"])
		self.assertEqual(round(due), round(self.bill(run).payable_within_due))
		paid = cashdesk.post(ref, 5000)
		self.assertEqual(round(paid["balance_after"]), round(due - 5000))
		s = cashdesk.state()
		self.assertEqual((s["totals"]["receipts"], s["totals"]["cash"], s["recent"][0]["house"]), (1, 5000, "N 2308"))
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.PermissionError):
			cashdesk.state()

	# ---- reading review
	def test_review_queue_lists_flagged_readings_with_what_approving_does(self):
		self.september_posted_with_arrears()
		cycle = f"{SOC}-Electricity-2026-10"
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01"}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading, r.reading_date = 9999, "2026-10-05"
		r.save()
		self.assertEqual(r.status, "Flagged")
		q = reading_review.queue(cycle)
		self.assertEqual([x.name for x in q["rows"]], [r.name])
		self.assertEqual(q["rows"][0].house, "N 2308")
		self.assertIn("billed as read", q["rows"][0].consequence)
		self.assertEqual(q["counts"]["waiting"], 1)
		readings.approve_reading(r.name, "Checked")
		self.assertEqual(reading_review.queue(cycle)["rows"], [])

	# ---- bill run wizard
	def test_wizard_lists_a_very_high_bill_and_holding_it_needs_recalculation(self):
		self.october()
		self.month_reading("2026-11-01", 3905 + 2500)
		self.fpa("2026-11-01", 1.0)
		run = self.make_run("2026-11-01", "2026-12-01")
		d = billrun_wizard.load(run.name)
		self.assertEqual([s["done"] for s in d["steps"]], [True, True, True, False, False, False])
		self.assertEqual([(e["kind"], e["units"]) for e in d["exceptions"]], [("Very high bill", 2500)])
		self.assertEqual(d["left"], 1)
		self.assertTrue(d["groups"])
		key = d["exceptions"][0]["key"]
		d = billrun_wizard.decide(run.name, key, "Accepted")
		self.assertEqual((d["left"], d["steps"][3]["done"]), (0, True))
		d = billrun_wizard.decide(run.name, key, "Held")
		run.reload()
		self.assertEqual([x.connection for x in run.exclusions], [self.conn])
		self.assertEqual(run.calculation_status, "Not calculated")
		self.assertEqual(d["exceptions"], [])
		frappe.set_user(BILLING_OFFICER)
		billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Bill Run", run.name, "bill_count"), 0)

	def test_wizard_decides_many_at_once(self):
		"""Increment 53: Accept, Hold or Undo for every exception shown, in one step. An account that couldn't be billed isn't accepted;
		an account is held out of the run once, however many of its keys are held."""
		self.october()
		self.month_reading("2026-11-01", 3905 + 2500)
		self.fpa("2026-11-01", 1.0)
		run = self.make_run("2026-11-01", "2026-12-01")
		key = billrun_wizard.load(run.name)["exceptions"][0]["key"]
		problem = f"problem:{self.conn}"
		frappe.set_user(BILLING_OFFICER)
		d = billrun_wizard.decide_many(run.name, json.dumps([key, problem]), "Accepted")
		self.assertEqual((d["changed"], d["left"], d["steps"][3]["done"]), (1, 0, True))
		self.assertNotIn(problem, billrun_wizard.review_of(frappe.get_doc("Bill Run", run.name)))
		d = billrun_wizard.decide_many(run.name, [key], "")
		self.assertEqual((d["changed"], d["left"]), (1, 1))
		d = billrun_wizard.decide_many(run.name, json.dumps([key, problem]), "Held")
		frappe.set_user("Administrator")
		run.reload()
		self.assertEqual(([x.connection for x in run.exclusions], run.calculation_status), ([self.conn], "Not calculated"))
		with self.assertRaises(frappe.ValidationError):
			billrun_wizard.decide_many(run.name, [key], "Approved")

	def test_wizard_pages_exist(self):
		for page in ("ub-billrun", "ub-cash", "ub-review", "ub-tariff", "ub-consumer-360", "ub-recovery"):
			self.assertTrue(frappe.db.exists("Page", page), page)
