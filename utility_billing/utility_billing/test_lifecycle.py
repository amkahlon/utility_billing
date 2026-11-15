# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 23 part A: merging and splitting accounts (FS-WF-35; user decisions 2026-11-16), account changes only through service
requests (FS-MST-16) and the consumer told at each request step.

House N 2308 (spec test case 1): after September the account owes Rs 1,104 and its meter TST-BIL-M1 was last billed at 3,641."""

import frappe
from frappe.utils import nowdate

from utility_billing.utility_billing import account, servicereq
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing import test_network_services as tns  # the module, so its tests aren't collected here again
from utility_billing.utility_billing.testing import BILLING_MANAGER


class IntegrationTestLifecycle(BillingCase):
	request = tns.IntegrationTestServiceRequests.request
	step = tns.IntegrationTestServiceRequests.step
	finish = tns.IntegrationTestServiceRequests.finish
	attach_all = tns.IntegrationTestServiceRequests.attach_all

	def setUp(self):
		super().setUp()
		servicereq.ensure_types(SOC)
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		frappe.db.set_value("Society", SOC, "changes_through_requests", 1)
		frappe.clear_document_cache("Society", SOC)

	def other_account(self):
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2308-A"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-OTHER", "society": SOC, "utility": "Electricity"}).insert().name
		return frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": self.consumer, "utility": "Electricity",
		                       "consumer_category": self.cat, "status": "Active",
		                       "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 0}]}).insert().name

	# ---- A1 merge
	def test_a_merge_moves_balance_and_deposits_and_closes_the_account(self):
		self.september_posted_with_arrears()
		keep = self.other_account()
		dep = frappe.get_doc({"doctype": "Security Deposit", "connection": self.conn, "deposit_type": "Received", "amount": 5000, "mode": "Cash",
		                      "posting_date": nowdate()}).insert().name
		sr = self.request("MERGE", merge_into=keep, effective_date=nowdate())
		self.assertEqual([s.step_kind for s in sr.steps], ["Approval", "Field"])
		self.attach_all(sr)
		self.step(sr)
		with self.assertRaises(frappe.ValidationError):
			self.step(sr, reading=3700)  # 59 units not billed yet: they are billed on the account itself first
		self.step(sr, reading=3641)
		self.assertEqual(sr.status, "Completed", sr.result_note)
		gone = frappe.get_doc("Connection", self.conn)
		self.assertEqual((gone.status, gone.merged_into, gone.current_meter), ("Closed", keep, None))
		self.assertEqual((account.balance(self.conn), account.balance(keep)), (0, 1104))
		self.assertEqual(frappe.get_all("Customer Ledger Entry", filters={"voucher_no": sr.name}, pluck="entry_type"), ["Transfer", "Transfer"])
		self.assertEqual(frappe.db.get_value("Security Deposit", dep, ["connection", "transferred_from"]), (keep, self.conn))
		self.assertEqual(frappe.db.get_value("Meter", "TST-BIL-M1", "status"), "Removed")
		self.assertEqual([c.change_type for c in frappe.get_doc("Connection", keep).changes], ["Merged"])
		self.assertIn("1,104.00", sr.result_note)

	def test_a_merge_needs_another_active_account_of_the_same_utility(self):
		with self.assertRaises(frappe.ValidationError):
			self.request("MERGE", merge_into=self.conn)
		with self.assertRaises(frappe.ValidationError):
			self.request("MERGE")

	# ---- A1 split
	def test_a_split_opens_a_new_account_and_the_original_keeps_its_balance(self):
		self.september_posted_with_arrears()
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2308 upper"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-SPLIT", "society": SOC, "utility": "Electricity"}).insert().name
		sr = self.request("SPLIT", property=prop, new_load_kw=2, new_meter=meter, new_meter_reading=0, effective_date=nowdate())
		new = sr.new_connection
		self.assertEqual(frappe.db.get_value("Connection", new, ["status", "split_from", "property"]), ("Applied", self.conn, prop))
		self.attach_all(sr)
		self.finish(sr)
		self.assertEqual(sr.status, "Completed", sr.result_note)
		self.assertEqual(frappe.db.get_value("Connection", new, ["status", "current_meter"]), ("Active", meter))
		self.assertEqual((account.balance(self.conn), account.balance(new)), (1104, 0))
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "status"), "Active")

	def test_a_cancelled_split_closes_the_applied_account(self):
		sr = self.request("SPLIT", new_load_kw=2)
		servicereq.cancel(sr.name, "Applicant withdrew")
		self.assertEqual(frappe.db.get_value("Connection", sr.new_connection, "status"), "Closed")

	# ---- A2 changes only through service requests
	def test_an_active_account_changes_only_through_a_request(self):
		frappe.set_user(BILLING_MANAGER)
		try:
			c = frappe.get_doc("Connection", self.conn)
			c.sanctioned_load_kw = 9
			with self.assertRaises(frappe.ValidationError):
				c.save()
			c.reload()
			c.phase = "Three"  # other details are still edited on the form
			c.save()
		finally:
			frappe.set_user("Administrator")
		frappe.db.set_value("Society", SOC, "changes_through_requests", 0)
		frappe.clear_document_cache("Society", SOC)
		frappe.set_user(BILLING_MANAGER)
		try:
			c = frappe.get_doc("Connection", self.conn)
			c.sanctioned_load_kw = 9
			c.save()  # the society switched the rule off
		finally:
			frappe.set_user("Administrator")
		sr = self.request("LOAD", new_load_kw=12)
		self.attach_all(sr)
		frappe.db.set_value("Society", SOC, "changes_through_requests", 1)
		frappe.clear_document_cache("Society", SOC)
		self.finish(sr)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "sanctioned_load_kw"), 12)  # a request still makes the change

	# ---- A3 the consumer is told at each step
	def test_the_consumer_hears_of_each_step(self):
		sr = self.request("LOAD", new_load_kw=12)
		self.attach_all(sr)
		self.finish(sr)
		bodies = frappe.get_all("Message Log", filters={"reference_doctype": "Service Request", "reference_name": sr.name, "event": "request.updated"},
		                        fields=["body", "status"], order_by="creation")
		self.assertEqual(len(bodies), 1 + len(sr.steps))  # received, then each step (the last completes it)
		texts = [b.body or "" for b in bodies if b.status != "Skipped"]
		if texts:
			self.assertIn("received", texts[0])
			self.assertIn("completed", texts[-1])

	# ---- A4 check meters on reading routes
	def test_check_meters_are_read_on_their_route_and_give_the_transformer_input(self):
		from utility_billing.utility_billing import losses, readings  # noqa: PLC0415
		from utility_billing.utility_billing.testing import BILLING_OFFICER  # noqa: PLC0415

		gs = frappe.get_doc({"doctype": "Grid Station", "society": SOC, "station_code": "GS9", "station_name": "Grid 9"}).insert().name
		feeder = frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "F9", "feeder_name": "Feeder 9", "grid_station": gs}).insert().name
		check = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-CHECK", "society": SOC, "utility": "Electricity",
		                        "meter_type": "Bulk / Check Meter", "multiplier": 1}).insert().name
		tx = frappe.get_doc({"doctype": "Transformer", "society": SOC, "transformer_code": "T9", "feeder": feeder, "kva_rating": 100,
		                     "check_meter": check}).insert().name
		frappe.db.set_value("Connection", self.conn, {"transformer": tx, "feeder": feeder})
		route = frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": "R9", "enabled": 1,
		                        "stops": [{"sequence": 1, "stop_type": "Connection", "connection": self.conn},
		                                  {"sequence": 2, "stop_type": "Check Meter", "meter": check}]}).insert().name
		for month, present, mine in (("2026-08-01", 1000, 2830), ("2026-09-01", 1900, 3641)):
			cycle = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month,
			                        "route_assignments": [{"route": route, "reader": BILLING_OFFICER}]}).insert().name
			self.month_reading(month, mine, close=False)
			cm = frappe.get_doc("Check Meter Reading", {"reading_cycle": cycle})
			self.assertEqual((cm.route, cm.route_sequence, cm.assigned_reader, cm.transformer, cm.status), ("R9", 2, BILLING_OFFICER, tx, "Pending"))
			cm.present_reading, cm.reading_date = present, f"{month[:8]}05"
			cm.save()
			readings.close_cycle(cycle)
		self.assertEqual((cm.previous_reading, cm.units, cm.status), (1000, 900, "Read"))
		self.fpa("2026-09-01", 0)
		self.post(self.make_run("2026-09-01", "2026-10-01"))
		row = next(r for r in losses.network_losses(SOC, "2026-09-01", "Transformer") if r["item"] == tx)
		self.assertEqual((row["input_kwh"], row["billed_kwh"], row["loss_kwh"]), (900, 811, 89))
		self.assertFalse([r for r in losses.network_losses(SOC, "2026-09-01", "Feeder") if r["item"] == feeder and r["input_kwh"]])
		# A5 the reading dashboard of the September cycle
		from utility_billing.utility_billing.reading_dashboard import get_reading_dashboard  # noqa: PLC0415

		d = get_reading_dashboard(SOC, f"{SOC}-Electricity-2026-09")
		self.assertEqual((d["cards"]["total"], d["cards"]["read"], d["cards"]["percent"], d["check_read"]), (1, 1, 100.0, 1))
		self.assertEqual([(r["label"], r["total"]) for r in d["by_route"]], [("R9", 1)])
		self.assertEqual(d["by_reader"][0]["total"], 1)
