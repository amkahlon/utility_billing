# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Net metering (increment 28): the three methods, carry-forward, charges on imported or net units, the society's monthly rebate pool,
export readings, opening solar credit, and the spec's test case 2 (house 59-C, September 2026) reproduced exactly."""

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from utility_billing.install import create_default_exception_codes, create_default_utilities
from utility_billing.utility_billing import billing, netmeter, readings
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, GENERAL_MANAGER, act, approve, make_users

SOC = "TST-NM"


class UnitTestNetMeter(UnitTestCase):
	def test_test_case_2_pool(self):
		f = netmeter.pool_figures(913_289, 787_200, 408_159, 213_600)
		self.assertEqual((f["pool_units"], f["solar_used_inside"], f["losses_units"]), (126_089, 194_559, 68_470))
		self.assertEqual(round(float(f["credit_factor"]), 4), 0.3089)
		r = netmeter.compute(netmeter.POOL, 979, 908, pool_units=126_089, pool_exports=408_159)
		# 908 × 126,089 ÷ 408,159 = 280.5005: the exact share, not the rounded factor (0.3089 × 908 = 280.48 would round to 280)
		self.assertEqual((r.credited_units, r.billed_units, r.carried_out), (281, 698, 0))
		self.assertEqual(netmeter.compute(netmeter.POOL, 979, 908, pool_units=126_089, pool_exports=408_159, rounding=netmeter.DOWN).credited_units, 280)

	def test_net_units_carry_forward(self):
		m1 = netmeter.compute(netmeter.NET, 300, 500)
		self.assertEqual((m1.billed_units, m1.set_off, m1.carried_out), (0, 300, 200))
		m2 = netmeter.compute(netmeter.NET, 400, 100, carried_in=m1.carried_out)
		self.assertEqual((m2.billed_units, m2.set_off, m2.carried_out), (100, 300, 0))

	def test_buyback(self):
		r = netmeter.compute(netmeter.BUYBACK, 979, 908, carried_in=50, buyback_rate=19.32)
		self.assertEqual((r.billed_units, r.buyback_credit, r.carried_out), (979, Decimal("17542.56"), 50))

	def test_credit_growing_for_a_year(self):
		from utility_billing.utility_billing import revenue_assurance as ra  # noqa: PLC0415

		self.assertTrue(ra.credit_growing(list(range(13, 0, -1))))  # 13 bills, each carrying more than the one before
		self.assertFalse(ra.credit_growing(list(range(12, 0, -1))))  # only 11 increases
		self.assertFalse(ra.credit_growing([13, 12, 11, 11, 9, 8, 7, 6, 5, 4, 3, 2, 1]))  # one month it didn't grow

	def test_rounding_rules(self):
		self.assertEqual([netmeter.round_units(Decimal("280.5"), rule) for rule in (netmeter.HALF_UP, netmeter.DOWN, netmeter.UP)], [281, 280, 281])
		self.assertEqual(netmeter.round_units(Decimal("280.4949"), netmeter.NO_ROUNDING), Decimal("280.49"))


class NetMeteringCase(IntegrationTestCase):
	"""Spec test case 2: house 59-C of a second society, Domestic NM tariff at Rs 67 flat, society charges, no FPA or QTA."""

	CHARGES = (("TST NM Consumption", 2700), ("TST NM Maintenance", 1200), ("TST NM Mosque", 450), ("TST NM E-Billing", 200),
	           ("TST NM Service", 200), ("TST NM Spray", 100))

	def setUp(self):
		create_default_utilities()
		create_default_exception_codes()
		make_users()
		for name, group, basis, order in (("TST NM Energy", "Electricity", "Per Unit", 10), ("TST NM Duty", "Government", "Per Unit", 30),
		                                  *((c, "Society", "Fixed", 40 + i) for i, (c, _r) in enumerate(self.CHARGES))):
			if not frappe.db.exists("Charge Type", name):
				frappe.get_doc({"doctype": "Charge Type", "charge_name": name, "charge_group": group, "calculation_basis": basis, "bill_order": order,
				                "bill_label": name.replace("TST NM ", "")}).insert()
		if not frappe.db.exists("Society", SOC):
			frappe.get_doc({"doctype": "Society", "society_name": "Net Metering Test Society", "short_name": SOC, "energy_charge_type": "TST NM Energy",
			                "charges_fpa": 0, "charges_qta": 0, "surcharge_rate": 10, "days_to_due_date": 8}).insert()
		frappe.db.set_value("Society", SOC, {"net_metering_method": netmeter.POOL, "nm_credit_rounding": netmeter.HALF_UP})
		self.cat = "Electricity-TST Domestic NM"
		if not frappe.db.exists("Consumer Category", self.cat):
			frappe.get_doc({"doctype": "Consumer Category", "category_name": "TST Domestic NM", "utility": "Electricity"}).insert()
		if not frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": self.cat, "docstatus": 1}):
			approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": self.cat, "effective_from": "2026-01-01",
			                        "pricing_method": "Flat rate", "flat_rate": 67, "buyback_rate": 20,
			                        "charges": [{"charge_type": c, "rate": r} for c, r in self.CHARGES]}).insert())
		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Mr. Asad Aftab"}).insert().name
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "59-C"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-NM-188329", "society": SOC, "utility": "Electricity",
		                        "meter_type": "Net Metering", "registers": 2}).insert().name
		self.conn = frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
		                            "consumer_category": self.cat, "status": "Active", "sanctioned_load_kw": 11,
		                            "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 16538, "start_export_reading": 20111}]}).insert().name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def method(self, method):
		frappe.db.set_value("Society", SOC, "net_metering_method", method)
		frappe.clear_document_cache("Society", SOC)

	def read(self, month, present, export, close=True):
		cycle = f"{SOC}-Electricity-{month[:7]}"
		if not frappe.db.exists("Reading Cycle", cycle):
			frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading, r.present_export_reading, r.reading_date = present, export, f"{month[:8]}08"
		r.save()
		if r.status == "Flagged":
			readings.approve_reading(r.name, "Confirmed with the consumer")
		if close:
			readings.close_cycle(cycle)
		return frappe.get_doc("Meter Reading", r.name)

	def pool(self, month="2026-09-01", approve_it=True, **figures):
		frappe.set_user(BILLING_OFFICER)
		p = frappe.get_doc({"doctype": "Solar Rebate Pool", "society": SOC, "utility": "Electricity", "billing_month": month,
		                    **(figures or {"residents_import_units": 913_289, "disco_import_units": 787_200,
		                                   "residents_export_units": 408_159, "disco_export_units": 213_600})}).insert()
		frappe.set_user("Administrator")
		if approve_it:
			act(p, "Submit for Approval", BILLING_OFFICER)
			act(p, "Approve", GENERAL_MANAGER)
			p.reload()
		return p

	def make_run(self, month, issue):
		frappe.set_user(BILLING_OFFICER)
		run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": month, "issue_date": issue}).insert()
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


class IntegrationTestNetMetering(NetMeteringCase):
	def test_case_2_september_bill_exactly(self):
		r = self.read("2026-09-01", 17_517, 21_019)
		self.assertEqual((r.net_metering, r.previous_export_reading, r.units, r.export_units), (1, 20_111, 979, 908))
		run = self.make_run("2026-09-01", "2026-10-11")
		self.assertIn("solar rebate pool", run.problems[0].problem)  # no approved pool yet: solar accounts wait for it
		pool = self.pool()
		self.assertEqual((pool.pool_units, pool.solar_used_inside, pool.losses_units, round(pool.credit_factor, 4)), (126_089, 194_559, 68_470, 0.3089))
		self.assertIn("126,089", pool.notice)
		frappe.set_user(BILLING_OFFICER)
		billing.calculate_run(run.name)  # the same run, recalculated once the pool is approved (FS-RUN-02)
		frappe.set_user("Administrator")
		run.reload()
		self.assertEqual(len(run.problems), 0)
		b = self.bill(run)
		self.assertEqual((b.nm_method, b.import_units, b.export_units, b.credited_units, b.units), (netmeter.POOL, 979, 908, 281, 698))
		energy = b.lines[0]
		self.assertEqual((energy.label, energy.quantity, energy.amount), ("Energy", 698, 46_766))
		self.assertEqual(sum(l.amount for l in b.lines if l.charge_group == "Society"), 4_850)
		self.assertEqual((b.current_amount, b.arrears, b.payable_within_due, b.surcharge_amount, b.payable_after_due), (51_616, 0, 51_616, 5_162, 56_778))
		self.assertEqual((b.solar_pool, b.units_carried_out), (pool.name, 0))
		run = self.post(run)
		with self.assertRaises(frappe.ValidationError):  # a pool a posted bill used can't be cancelled
			act(pool, "Cancel", GENERAL_MANAGER)

	def test_net_units_carry_forward_across_bills(self):
		self.method(netmeter.NET)
		self.read("2026-08-01", 16_838, 20_611)  # 300 imported, 500 exported
		aug = self.bill(self.post(self.make_run("2026-08-01", "2026-09-11")))
		self.assertEqual((aug.units, aug.units_set_off, aug.units_carried_out), (0, 300, 200))
		self.assertEqual(aug.lines[0].amount, 0)
		self.read("2026-09-01", 17_238, 20_711)  # 400 imported, 100 exported, 200 brought forward
		sep = self.bill(self.make_run("2026-09-01", "2026-10-11"))
		self.assertEqual((sep.units_carried_in, sep.units, sep.units_carried_out), (200, 100, 0))
		self.assertEqual(sep.lines[0].amount, 6_700)

	def test_buyback_and_charges_on_imported_units(self):
		self.method(netmeter.BUYBACK)
		duty = frappe.get_doc("Tariff", frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": self.cat, "docstatus": 1}))
		frappe.db.set_value("Charge Type", "TST NM Duty", "nm_units_basis", netmeter.IMPORTED)
		frappe.get_doc({"doctype": "Tariff Charge", "parent": duty.name, "parenttype": "Tariff", "parentfield": "charges",
		                "charge_type": "TST NM Duty", "rate": 2}).db_insert()
		frappe.clear_document_cache("Tariff", duty.name)
		self.read("2026-09-01", 17_517, 21_019)
		b = self.bill(self.make_run("2026-09-01", "2026-10-11"))
		lines = {l.label: l for l in b.lines}
		self.assertEqual((b.units, lines["Energy"].amount), (979, 979 * 67))
		self.assertEqual((lines["Solar export credit"].amount, b.buyback_credit), (-18_160, 18_160))  # 908 × Rs 20
		self.assertEqual((lines["Duty"].quantity, lines["Duty"].amount), (979, 1_958))

	def test_export_reading_checks_and_estimates(self):
		cycle = f"{SOC}-Electricity-2026-09"
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-09-01"}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading, r.present_export_reading, r.reading_date = 17_517, 20_000, "2026-09-08"  # below the previous 20,111
		r.save()
		self.assertEqual((r.status, r.export_units), ("Flagged", 0))
		self.assertIn("lower than the previous", r.checks)
		r.present_reading, r.reading_date, r.present_export_reading = 0, None, 0
		r.exception_code = frappe.get_all("Reading Exception Code", pluck="name", limit=1)[0]
		r.save()
		readings.estimate_unread(cycle)
		r.reload()
		self.assertEqual((r.status, r.export_units), ("Estimated", 0))
		self.assertIn("Export not estimated", r.checks)

	def test_opening_solar_credit_is_brought_forward(self):
		self.method(netmeter.NET)
		imp = frappe.get_doc({"doctype": "Opening Balance Import", "society": SOC, "as_of_date": "2026-07-31",
		                      "rows": [{"reference_no": frappe.db.get_value("Connection", self.conn, "reference_no"), "solar_credit_units": 150}]})
		imp.flags.ignore_validate = imp.flags.ignore_mandatory = imp.flags.ignore_links = True
		imp.insert()
		frappe.db.set_value("Opening Balance Import", imp.name, "docstatus", 1)
		frappe.db.set_value("Opening Balance Row", {"parent": imp.name}, "connection", self.conn)
		self.read("2026-09-01", 17_517, 21_019)
		b = self.bill(self.make_run("2026-09-01", "2026-10-11"))
		self.assertEqual((b.units_carried_in, b.units, b.units_carried_out), (150, 0, 79))  # 979 − (908 + 150) → 0, 79 carried

	def test_pool_figures_from_readings(self):
		self.read("2026-09-01", 17_517, 21_019)
		f = netmeter.residents_figures(SOC, "Electricity", "2026-09-01")
		self.assertEqual((f["residents_import_units"], f["residents_export_units"], f["solar_accounts"]), (979, 908, 1))
		with self.assertRaises(frappe.ValidationError):
			self.pool(approve_it=False, residents_import_units=-1, disco_import_units=0, residents_export_units=0)
		p = self.pool(approve_it=False)
		with self.assertRaises(frappe.ValidationError):  # one pool a month
			self.pool(approve_it=False)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):  # the maker can't approve
			act(p, "Submit for Approval", BILLING_OFFICER)
			act(p, "Approve", BILLING_OFFICER)

	def test_bill_prints_imports_exports_and_credit(self):
		import json  # noqa: PLC0415

		from utility_billing.utility_billing import bill_designer, bill_print, mis, opening  # noqa: PLC0415

		self.read("2026-09-01", 17_517, 21_019)
		pool = self.pool()
		run = self.post(self.make_run("2026-09-01", "2026-10-11"))
		b = self.bill(run)
		html = bill_print.bill_html(b.name)  # the fixed layout
		for text in ("Export (solar)", "20,111", "21,019", "908", "281 credited", "0.3089", "Units billed", "698", "126,089"):
			self.assertIn(text, html, text)
		# a designed template: the reading block's export row and the solar block
		layout = {"sections": [{"rows": [{"cells": [{"width": 100, "blocks": [{"type": "reading"}, {"type": "solar", "options": {"style": "Table"}}]}]}]}]}
		designed = bill_designer.render_bill(b, bill_designer._settings_from({}, layout))
		for text in ("Export (solar)", "Units credited", "281", "0.3089", "Carried forward", "126,089"):
			self.assertIn(text, designed, text)
		# the register report
		data = mis.REPORTS["net_metering_register"]["fn"](frappe._dict(society=SOC, month="2026-09-01"))["data"]
		self.assertEqual([(d["connection"], d["export_units"], d["credited_units"], d["units_billed"], d["state"]) for d in data],
		                 [(self.conn, 908, 281, 698, "Posted")])
		# a non-solar bill prints none of it
		self.assertNotIn("Export (solar)", bill_designer.render_bill(bill_designer.sample_bill(SOC), bill_designer._settings_from({}, {
			"sections": [{"rows": [{"cells": [{"width": 100, "blocks": [{"type": "reading"}]}]}]}]})))
		# opening balances read a solar credit column
		rows = opening.rows_from_table([["Reference No", "Arrears", "Solar Credit Units"], ["1000000016", "0", "150"]])
		self.assertEqual(rows[0]["solar_credit_units"], 150)
		self.assertEqual(json.loads(json.dumps(bill_print.snapshot_of(b)))["solar_notice"], pool.notice)

	def test_meter_change_credits_the_old_meters_last_exports(self):
		"""The defect found after the first merge: units a replaced solar meter exported after its last reading were never credited."""
		self.method(netmeter.NET)
		self.read("2026-09-01", 17_517, 21_019)
		conn = frappe.get_doc("Connection", self.conn)
		new = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-NM-NEW", "society": SOC, "utility": "Electricity", "meter_type": "Net Metering",
		                      "registers": 2}).insert().name
		conn.meters[0].update({"removed_on": "2026-09-20", "end_reading": 17_600, "end_export_reading": 21_200})  # 83 imported, 181 exported since
		conn.append("meters", {"meter": new, "installed_on": "2026-09-20", "start_reading": 0, "start_export_reading": 0})
		conn.save()
		r = self.read("2026-10-01", 100, 250)
		self.assertEqual((r.meter, r.old_meter_units, r.old_meter_export_units), (new, 83, 181))
		self.assertEqual((r.units, r.export_units), (183, 431))  # 83 + 100 imported, 181 + 250 exported
		b = self.bill(self.make_run("2026-10-01", "2026-11-11"))
		self.assertEqual((b.import_units, b.export_units, b.units, b.units_carried_out), (183, 431, 0, 248))
		conn.reload()
		conn.meters[1].update({"removed_on": "2026-10-20", "end_export_reading": -1})
		conn.meters[1].end_export_reading = 0.5
		conn.meters[1].start_export_reading = 5
		with self.assertRaises(frappe.ValidationError):  # an end export reading below the start
			conn.save()

	def test_losses_ra_consumer_360_and_api_know_solar(self):
		from utility_billing.api import v1  # noqa: PLC0415
		from utility_billing.utility_billing import consumer360, losses  # noqa: PLC0415
		from utility_billing.utility_billing import revenue_assurance as ra  # noqa: PLC0415

		self.method(netmeter.NET)
		self.read("2026-09-01", 17_517, 21_019)  # 979 imported, 908 exported
		b = self.bill(self.post(self.make_run("2026-09-01", "2026-10-11")))
		self.assertEqual(b.units, 71)
		# line losses: energy in = DISCO import − export to the DISCO + residents' solar; delivered = residents' imports, not the 71 billed
		frappe.get_doc({"doctype": "DISCO Bill", "society": SOC, "billing_month": "2026-09-01", "units_kwh": 900, "export_units_kwh": 700,
		                "bill_no": "D-NM-09"}).insert()
		loss = losses.society_loss(SOC, "2026-09-01")
		self.assertEqual((loss["input_kwh"], loss["billed_kwh"], loss["loss_kwh"], loss["solar_export_kwh"]), (1108, 979, 129, 908))
		# revenue assurance: exports beyond the solar capacity, and an export reading on a meter that isn't net metering
		frappe.db.set_value("Connection", self.conn, "solar_capacity_kw", 2)  # 2 kW × 6 h × 30 days = 360 units at most
		ra.run_checks(SOC, "2026-09-01")
		found = frappe.get_all("RA Finding", filters={"society": SOC, "check_type": "RA-11 Solar export anomaly"}, fields=["subject_type", "details"])
		self.assertEqual([f.subject_type for f in found], ["Connection"])
		self.assertIn("at most about 360", found[0].details)
		frappe.db.set_value("Meter", frappe.db.get_value("Connection", self.conn, "current_meter"), "meter_type", "Three Phase")
		ra.run_checks(SOC, "2026-09-01")
		self.assertEqual(frappe.db.count("RA Finding", {"society": SOC, "check_type": "RA-11 Solar export anomaly", "subject_type": "Meter"}), 1)
		# Consumer 360 and the billing API
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		v = consumer360.view(consumer, self.conn)
		solar = v["overview"]["bill"]["solar"]
		self.assertEqual((solar["imported"], solar["exported"], solar["credited"], solar["carried_out"]), (979, 908, 908, 0))
		row = frappe.get_all("Utility Bill", filters={"name": b.name}, fields=["*"])[0]
		self.assertEqual(v1._bill_view(row)["solar"], {"method": netmeter.NET, "imported_units": 979, "exported_units": 908, "credited_units": 908,
		                                               "units_carried_forward": 0, "buyback_credit": 0})
		row.nm_method = None
		self.assertIsNone(v1._bill_view(row)["solar"])
