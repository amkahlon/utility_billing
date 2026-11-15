# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for revenue assurance (FS-RA-01 to 10): the checks on a society's data, findings as cases, the report.

History is written straight into posted bills (the checks only read them), so each check can be set up in a few lines.
Society TST-RA: five houses of 10 marla on a residential category, 300 units a month from April to September 2026."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing import losses, mis, revenue_assurance as ra
from utility_billing.utility_billing.testing import BILLING_MANAGER, CASHIER, RECOVERY_OFFICER, make_users

SOC = "TST-RA"
OCT = "2026-10-01"
HISTORY = ["2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]


class IntegrationTestRevenueAssurance(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		make_users()
		if not frappe.db.exists("Society", SOC):
			frappe.get_doc({"doctype": "Society", "society_name": "Revenue Assurance Test Society", "short_name": SOC}).insert()
		self.cat = "Electricity-TST RA Residential"
		if not frappe.db.exists("Consumer Category", self.cat):
			frappe.get_doc({"doctype": "Consumer Category", "category_name": "TST RA Residential", "utility": "Electricity"}).insert()
		self.n = 0
		self.houses = [self.house() for _ in range(5)]
		for month in HISTORY:
			for c in self.houses:
				self.bill(c, month, 300)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ---- helpers
	def house(self, meter=True, property_type="House", load=0, status="Active"):
		self.n += 1
		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": f"RA Owner {self.n}"}).insert().name
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"RA-{self.n}", "area": 10, "property_type": property_type}).insert().name
		values = {"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
		          "consumer_category": self.cat, "status": status, "sanctioned_load_kw": load}
		if meter:
			m = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-RA-M{self.n}", "society": SOC, "utility": "Electricity"}).insert().name
			values["meters"] = [{"meter": m, "installed_on": "2026-01-01", "start_reading": 0}]
		return frappe.get_doc(values).insert().name

	def bill(self, connection, month, units, kind="Actual"):
		b = frappe.get_doc({"doctype": "Utility Bill", "connection": connection, "society": SOC, "utility": "Electricity", "billing_month": month,
		                    "units": units, "current_amount": units * 50, "consumer_category": self.cat, "consumption_type": kind,
		                    "property": frappe.db.get_value("Connection", connection, "property"), "status": "Posted"})
		b.name, b.docstatus = frappe.generate_hash(length=12), 1
		b.db_insert()
		return b.name

	def posted_run(self, month, exclusions=()):
		r = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": month,
		                    "exclusions": [{"connection": c, "reason": "Test"} for c in exclusions]})
		r.name, r.docstatus = frappe.generate_hash(length=12), 1
		r.db_insert()
		for row in r.exclusions:
			row.parent, row.parenttype, row.parentfield = r.name, "Bill Run", "exclusions"
			row.db_insert()
		return r.name

	def findings(self, check=None):
		filters = {"society": SOC}
		if check:
			filters["check_type"] = ["like", f"{check}%"]
		return frappe.get_all("RA Finding", filters=filters, fields=["name", "check_type", "subject", "connection", "score", "amount_at_risk", "status"])

	def october(self, units=(80, 3000, 300, 300, 300)):
		for c, u in zip(self.houses, units, strict=True):
			self.bill(c, OCT, u)

	# ---- RA-01, RA-06, RA-10
	def test_a_drop_and_a_multiplier_jump_are_found(self):
		self.october()
		out = ra.run_checks(SOC, OCT)
		(drop,) = self.findings("RA-01")
		self.assertEqual((drop.connection, drop.score, drop.amount_at_risk, drop.status), (self.houses[0], 73, (300 - 80) * 50, "Open"))
		(mult,) = self.findings("RA-06")
		self.assertEqual(mult.connection, self.houses[1])
		self.assertIn("10 times", frappe.db.get_value("RA Finding", mult.name, "details"))
		self.assertEqual(out["new"], 2)
		self.assertEqual(self.findings("RA-10"), [])  # each account is reported once, under its clearest check

	def test_an_outlier_that_is_not_a_drop_or_a_multiplier(self):
		self.october(units=(300, 300, 300, 300, 400))  # a third more than usual: not a drop, not a multiplier step
		ra.run_checks(SOC, OCT)
		(f,) = self.findings("RA-10")
		self.assertEqual(f.connection, self.houses[4])

	def test_running_again_refreshes_and_never_reopens(self):
		self.october()
		ra.run_checks(SOC, OCT)
		again = ra.run_checks(SOC, OCT)
		self.assertEqual((again["new"], again["updated"], len(self.findings())), (0, 2, 2))
		f = frappe.get_doc("RA Finding", self.findings("RA-01")[0].name)
		f.update({"status": "Closed", "result": "No issue", "notes": "Family abroad for the summer"})
		f.save()
		ra.run_checks(SOC, OCT)
		self.assertEqual(frappe.db.get_value("RA Finding", f.name, "status"), "Closed")

	# ---- RA-02
	def test_an_active_account_left_out_of_a_posted_run(self):
		self.october()
		missing, excluded = self.house(), self.house()
		ra.run_checks(SOC, OCT)
		self.assertEqual(self.findings("RA-02"), [])  # no posted run yet: nothing is missing
		self.posted_run(OCT, exclusions=[excluded])
		ra.run_checks(SOC, OCT)
		self.assertEqual([f.connection for f in self.findings("RA-02")], [missing])

	# ---- RA-03
	def test_meter_records(self):
		self.october()
		frappe.get_doc({"doctype": "Meter", "serial_no": "tst ra m1", "society": SOC, "utility": "Electricity"}).insert()  # TST-RA-M1 again
		store = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-RA-LOOSE", "society": SOC, "utility": "Electricity"}).insert()
		frappe.db.set_value("Meter", store.name, "status", "Installed")  # installed, on no account
		no_meter = self.house(meter=False, status="Applied")
		frappe.db.set_value("Connection", no_meter, "status", "Active")  # e.g. switched on by hand without its meter
		ra.run_checks(SOC, OCT)
		texts = {f.subject: frappe.db.get_value("RA Finding", f.name, "details") for f in self.findings("RA-03")}
		self.assertIn("2 times", texts["TST-RA-M1"])
		self.assertIn("TST-RA-LOOSE", texts)
		self.assertIn(no_meter, texts)
		# found in the browser check: a disconnected account keeps its meter, which is not a finding
		meter = frappe.db.get_value("Connection", self.houses[1], "current_meter")
		frappe.db.set_value("Connection", self.houses[1], "status", "Disconnected")
		frappe.db.delete("RA Finding", {"society": SOC})
		ra.run_checks(SOC, OCT)
		self.assertNotIn(meter, {f.subject for f in self.findings("RA-03")})

	# ---- RA-04
	def test_load_and_use_that_dont_fit_the_category(self):
		self.october()
		big = self.house(load=12)
		shop = self.house(property_type="Shop")
		over = self.house(load=3)
		frappe.db.set_value("Connection", over, "last_mdi_kw", 4.5)
		ra.run_checks(SOC, OCT)
		found = {f.connection: frappe.db.get_value("RA Finding", f.name, "details") for f in self.findings("RA-04")}
		self.assertEqual(set(found), {big, shop, over})
		self.assertIn("12 kW", found[big])
		self.assertIn("shop", found[shop])
		self.assertIn("above the sanctioned 3 kW", found[over])

	# ---- RA-05
	def test_estimates_beyond_the_allowed_months(self):
		frappe.db.sql("update `tabUtility Bill` set consumption_type = 'Estimated' where connection = %s and billing_month in ('2026-08-01', '2026-09-01')",
		              self.houses[2])
		self.october()
		frappe.db.sql("update `tabUtility Bill` set consumption_type = 'Estimated' where connection = %s and billing_month = %s", (self.houses[2], OCT))
		ra.run_checks(SOC, OCT)
		(f,) = self.findings("RA-05")
		self.assertEqual(f.connection, self.houses[2])
		frappe.db.set_value("Society", SOC, "ra_max_estimates", 3)
		frappe.db.delete("RA Finding", {"society": SOC})
		ra.run_checks(SOC, OCT)
		self.assertEqual(self.findings("RA-05"), [])

	# ---- RA-06 second rule, RA-07, RA-08
	def test_a_reading_taken_with_another_multiplier(self):
		self.october(units=(300, 300, 300, 300, 300))
		meter = frappe.db.get_value("Connection", self.houses[3], "current_meter")
		frappe.db.set_value("Meter", meter, "multiplier", 1)
		r = frappe.get_doc({"doctype": "Meter Reading", "connection": self.houses[3], "society": SOC, "billing_month": OCT, "meter": meter, "multiplier": 10})
		r.name = frappe.generate_hash(length=12)
		r.db_insert()
		ra.run_checks(SOC, OCT)
		(f,) = self.findings("RA-06")
		self.assertEqual((f.subject, f.score), (meter, 90))

	def test_network_imbalance(self):
		self.october(units=(300, 300, 300, 300, 300))
		row = {"item": "F-1", "name": "Feeder 1", "input_kwh": 2000, "billed_kwh": 1500, "loss_pct": 25.0, "status": losses.HIGH}
		with patch.object(losses, "network_losses", side_effect=lambda s, m, by: [row] if by == "Feeder" else []):
			ra.run_checks(SOC, OCT)
		(f,) = self.findings("RA-07")
		self.assertEqual((f.subject, f.score), ("F-1", 75))

	def test_unusual_reversals_by_one_user(self):
		self.october(units=(300, 300, 300, 300, 300))
		for user, amount in (("a@x.test", 1000), ("b@x.test", 1200), ("c@x.test", 900), ("d@x.test", 15000)):
			e = frappe.get_doc({"doctype": "Customer Ledger Entry", "connection": self.houses[0], "society": SOC, "posting_date": "2026-10-12",
			                    "entry_type": "Reversal", "amount": amount})
			e.name = frappe.generate_hash(length=12)
			e.db_insert()
			frappe.db.sql("update `tabCustomer Ledger Entry` set owner = %s where name = %s", (user, e.name))  # db_insert keeps the session user
		ra.run_checks(SOC, OCT)
		(f,) = self.findings("RA-08")
		self.assertEqual((f.subject, f.amount_at_risk), ("d@x.test", 15000))
		frappe.db.set_value("Society", SOC, "ra_adjustment_limit", 1100)
		ra.run_checks(SOC, OCT)
		self.assertEqual(sorted(f.subject for f in self.findings("RA-08")), ["b@x.test", "d@x.test"])

	# ---- RA-09: the case
	def test_a_finding_is_assigned_investigated_and_closed(self):
		self.october()
		ra.run_checks(SOC, OCT)
		f = frappe.get_doc("RA Finding", self.findings("RA-01")[0].name)
		self.assertTrue(f.finding_title.startswith("Consumption drop: "))
		f.assigned_to = RECOVERY_OFFICER
		f.save()
		self.assertEqual(f.status, "Assigned")
		f.status = "Closed"
		self.assertRaises(frappe.ValidationError, f.save)  # needs a result
		f.reload()
		f.update({"status": "Closed", "result": "Detection bill"})
		self.assertRaises(frappe.ValidationError, f.save)  # and the document or a note
		f.reload()
		f.update({"status": "Closed", "result": "Detection bill", "notes": "Meter bypass found; detection bill raised", "amount_recovered": 11000})
		frappe.set_user(RECOVERY_OFFICER)
		f.save()
		self.assertEqual((f.closed_by, bool(f.closed_on)), (RECOVERY_OFFICER, True))
		f.status = "Investigating"
		self.assertRaises(frappe.PermissionError, f.save)  # only a manager reopens
		frappe.set_user(BILLING_MANAGER)
		f.reload()
		f.status = "Investigating"
		f.save()
		self.assertIsNone(f.closed_by)

	def test_who_runs_the_checks_and_the_report(self):
		self.october()
		frappe.set_user(CASHIER)
		self.assertRaises(frappe.PermissionError, ra.run_now, SOC, OCT)
		frappe.set_user(BILLING_MANAGER)
		out = ra.run_now(SOC)  # the latest month with posted bills: October
		self.assertEqual((out["month"], out["new"]), (OCT, 2))
		frappe.set_user("Administrator")
		cols, data, *_rest = mis.run("revenue_assurance", {"society": SOC})
		self.assertEqual([(d["item"], d["findings"], d["open"]) for d in data], [("RA-01 Consumption drop", 1, 1), ("RA-06 Wrong multiplier", 1, 1), ("Total", 2, 2)])
		cols, data, *_rest = mis.run("revenue_assurance", {"society": SOC, "view": "Open Findings"})
		self.assertEqual(len(data), 2)
		self.assertEqual(ra.open_findings(SOC)[0], 2)
		from utility_billing.utility_billing import dashboard

		titles = [a["title"] for a in dashboard.attention_of(SOC, frappe.utils.getdate(OCT), frappe.utils.getdate("2026-10-20"))]
		self.assertIn("2 revenue assurance findings are open", titles)
