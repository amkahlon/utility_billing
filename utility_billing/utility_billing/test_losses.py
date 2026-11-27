# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 10: the DISCO bill, line losses, AT&C, transformer loading and DISCO reconciliation (FS-RPT-08, 30-34, 38).
Integration data is spec test case 1 (house N 2308): October billed 264 kWh = 22,260; September billed 811 kWh = 59,470."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_days, formatdate

from utility_billing.utility_billing import dashboard, losses, mis
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import BILLING_OFFICER, CASHIER


class UnitTestLossRules(UnitTestCase):
	def test_loss_percentage(self):
		self.assertEqual(losses.loss_pct(300, 264), 12.0)
		self.assertEqual(losses.loss_pct(412600, 389900), 5.5)  # the mock-up's feeder F-1
		self.assertIsNone(losses.loss_pct(0, 100))
		self.assertEqual(losses.loss_pct(100, 130), -30.0)

	def test_alert_levels_at_the_boundaries(self):
		cases = [((1000, 921), "Normal"), ((1000, 920), "Watch"), ((1000, 881), "Watch"), ((1000, 880), "High loss"),
		         ((1000, 1001), "Check data"), ((0, 50), "No input data"), ((None, 50), "No input data")]
		for (inp, billed), expected in cases:
			self.assertEqual(losses.status_of(inp, billed, 8, 12), expected, (inp, billed))
		self.assertEqual(losses.status_of(1000, 880, 8, 15), "Watch")  # the society's own levels apply

	def test_atc_is_one_minus_billing_times_collection_efficiency(self):
		r = losses.at_c(1000, 900, 100_000, 80_000)
		self.assertEqual((r["billing_efficiency"], r["collection_efficiency"], r["atc"]), (90.0, 80.0, 28.0))

	def test_collecting_old_arrears_cant_offset_this_months_loss(self):
		r = losses.at_c(1000, 900, 100_000, 150_000)  # collection efficiency is capped at 100%
		self.assertEqual((r["collection_efficiency"], r["atc"]), (100.0, 10.0))

	def test_atc_cannot_be_worked_out_without_its_parts(self):
		self.assertIsNone(losses.at_c(0, 900, 100, 100)["atc"])
		self.assertIsNone(losses.at_c(1000, 0, 0, 0)["atc"])
		self.assertEqual(losses.at_c(1000, 0, 100, 0)["atc"], 100.0)  # nothing billed in units but money was due and none collected

	def test_transformer_loading(self):
		self.assertEqual(losses.transformer_loading(90, 100), 100.0)  # 100 kVA x 0.9 = 90 kW
		self.assertEqual(losses.transformer_loading(5, 100), 5.6)
		self.assertIsNone(losses.transformer_loading(5, 0))
		self.assertEqual([losses.loading_status(p) for p in (50, 79.9, 80, 100, 100.1)], ["OK", "OK", "Heavy", "Heavy", "Overloaded"])


class IntegrationTestLosses(BillingCase):
	def setUp(self):
		super().setUp()
		self.feeder = ensure("Feeder", f"{SOC}-F1", {"society": SOC, "feeder_code": "F1", "feeder_name": "Test Feeder"})
		self.tx = ensure("Transformer", f"{SOC}-T1", {"society": SOC, "transformer_code": "T1", "feeder": self.feeder, "kva_rating": 100})
		frappe.db.set_value("Connection", self.conn, {"feeder": self.feeder, "transformer": self.tx, "sanctioned_load_kw": 5})
		frappe.db.set_value("Property", self.prop, "block", "N Block")

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def disco(self, month, units, rows=(), **amounts):
		return frappe.get_doc({"doctype": "DISCO Bill", "society": SOC, "billing_month": month, "units_kwh": units, "bill_no": f"D-{month}",
		                       "inputs": [dict(r) for r in rows], **amounts}).insert()

	def both_months(self):
		"""October: 300 kWh came in, 264 billed (12.0%, High loss); feeder 300 and transformer 290 kWh. September: 850 in, 811 billed."""
		self.october_posted()
		self.disco("2026-09-01", 850, energy_amount=50_000, fpa_amount=0, other_amount=5_000)
		return self.disco("2026-10-01", 300, [{"feeder": self.feeder, "input_kwh": 300}, {"transformer": self.tx, "input_kwh": 290}],
		                  energy_amount=20_000, fpa_amount=1_500, qta_amount=0, other_amount=3_000)

	# ----- the DISCO bill -----
	def test_disco_bill_totals_and_title(self):
		self.october_posted()
		b = self.disco("2026-10-17", 300, energy_amount=20_000, fpa_amount=-250, other_amount=3_000)  # an FPA credit is allowed
		self.assertEqual((str(b.billing_month), b.total_amount, b.disco_name or None), ("2026-10-01", 22_750, frappe.db.get_value("Society", SOC, "disco_name")))
		self.assertIn("Oct 2026", b.bill_title)

	def test_disco_bill_rules(self):
		self.october_posted()
		other_feeder = ensure("Feeder", "OTHER-F9", {"society": ensure("Society", "OTHER", {"society_name": "Losses Test Other Society", "short_name": "OTHER"}),
		                                            "feeder_code": "F9", "feeder_name": "Elsewhere"})
		self.disco("2026-10-01", 300)
		for bad in (
			{"month": "2026-10-20", "units": 300},                                                    # one bill per month
			{"month": "2026-11-01", "units": 0},                                                      # units required
			{"month": "2026-11-01", "units": 300, "energy_amount": -1},                               # energy can't be negative
			{"month": "2026-11-01", "units": 300, "rows": [{"feeder": self.feeder, "transformer": self.tx, "input_kwh": 1}]},
			{"month": "2026-11-01", "units": 300, "rows": [{"input_kwh": 1}]},                        # neither
			{"month": "2026-11-01", "units": 300, "rows": [{"feeder": self.feeder, "input_kwh": 1}] * 2},  # twice
			{"month": "2026-11-01", "units": 300, "rows": [{"feeder": other_feeder, "input_kwh": 1}]},  # another society's feeder
			{"month": "2026-11-01", "units": 300, "due_date": "2026-10-01"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.disco(bad["month"], bad["units"], bad.get("rows", ()), energy_amount=bad.get("energy_amount", 0), due_date=bad.get("due_date"))

	def test_society_alert_levels_are_checked(self):
		soc = frappe.get_doc("Society", SOC)
		self.assertEqual((soc.loss_watch_percent, soc.loss_high_percent), (8, 12))
		for watch, high in ((12, 8), (0, 12), (8, 101), (10, 10)):
			soc.loss_watch_percent, soc.loss_high_percent = watch, high
			with self.assertRaises(frappe.ValidationError):
				soc.save()

	# ----- losses -----
	def test_society_and_network_losses(self):
		self.both_months()
		oct_ = losses.society_loss(SOC, "2026-10-20")
		self.assertEqual((oct_["input_kwh"], oct_["billed_kwh"], oct_["loss_kwh"], oct_["loss_pct"], oct_["status"], oct_["source"]),
		                 (300, 264, 36, 12.0, "High loss", "DISCO bill"))
		sep = losses.society_loss(SOC, "2026-09-01")
		self.assertEqual((sep["billed_kwh"], sep["loss_pct"], sep["status"]), (811, 4.6, "Normal"))
		self.assertIsNone(losses.society_loss(SOC, "2026-08-01"))  # no DISCO bill that month
		f = losses.network_losses(SOC, "2026-10-01", "Feeder")
		self.assertEqual([(r["name"], r["connections"], r["input_kwh"], r["billed_kwh"], r["loss_pct"], r["status"]) for r in f],
		                 [("Test Feeder", 1, 300, 264, 12.0, "High loss")])
		t = losses.network_losses(SOC, "2026-10-01", "Transformer")
		self.assertEqual([(r["name"], r["input_kwh"], r["loss_pct"], r["status"], r["tone"]) for r in t], [("T1", 290, 9.0, "Watch", "amber")])

	def test_a_feeder_with_bills_but_no_input_row_says_so(self):
		self.october_posted()
		self.disco("2026-10-01", 300)  # no feeder rows
		rows = losses.network_losses(SOC, "2026-10-01", "Feeder")
		self.assertEqual([(r["name"], r["input_kwh"], r["loss_pct"], r["status"]) for r in rows], [("Test Feeder", 0, None, "No input data")])
		frappe.db.set_value("Connection", self.conn, "feeder", None)
		self.assertEqual(losses.network_losses(SOC, "2026-10-01", "Feeder")[0]["name"], "(no feeder)")

	def test_billed_more_than_came_in_is_flagged_not_hidden(self):
		self.october_posted()
		self.disco("2026-10-01", 200)  # fewer units on the bill than were billed
		self.assertEqual(losses.society_loss(SOC, "2026-10-01")["status"], "Check data")

	# ----- reports -----
	def rows(self, key, **f):
		return mis.REPORTS[key]["fn"](frappe._dict({"society": SOC, **f}))["data"]

	def test_line_loss_reports(self):
		self.both_months()
		r = self.rows("line_losses", month="2026-10-01")
		self.assertEqual([(x["name"], x["input_kwh"], x["billed_kwh"], x["loss_kwh"], x["loss_pct"], x["status"]) for x in r],
		                 [("Whole society: bulk meter against all consumers", 300, 264, 36, 12.0, "High loss"),
		                  ("Test Feeder", 300, 264, 36, 12.0, "High loss")])
		r = self.rows("line_losses", month="2026-10-01", group_by="Transformer")
		self.assertEqual((r[1]["name"], r[1]["loss_pct"], r[1]["status"]), ("T1", 9.0, "Watch"))
		empty = mis.REPORTS["line_losses"]["fn"](frappe._dict({"society": SOC, "month": "2026-08-01"}))  # nothing billed, no DISCO bill
		self.assertEqual(empty["data"], [])
		self.assertIn("No DISCO bill", empty["message"])

		rank = self.rows("loss_ranking", month="2026-10-01")
		self.assertEqual([(x["rank"], x["name"], x["loss_pct"]) for x in rank], [(1, "Test Feeder", 12.0)])
		trend = self.rows("loss_ranking", month="2026-10-01", show="12-Month Trend")
		self.assertEqual(len(trend), 12)
		self.assertEqual([(x["month"], x["loss_pct"], x["status"]) for x in trend[-2:]], [("Sep 2026", 4.6, "Normal"), ("Oct 2026", 12.0, "High loss")])
		self.assertEqual((trend[0]["loss_pct"], trend[0]["status"]), (None, "No input data"))
		self.assertTrue(mis.REPORTS["loss_ranking"]["fn"](frappe._dict({"society": SOC, "month": "2026-10-01", "show": "12-Month Trend"}))["chart"])

	def test_atc_report(self):
		self.both_months()
		r = self.rows("atc_losses", month="2026-10-01")
		octo, sep = r[-1], r[-2]
		# October: 264 of 300 kWh billed (88.0%); collections 58,366 exceed the 23,364 payable, so collection efficiency is capped at 100%.
		self.assertEqual((octo["billing_efficiency"], octo["collection_efficiency"], octo["atc"]), (88.0, 100.0, 12.0))
		# September: 811 of 850 billed (95.4%) but nothing was collected within September.
		self.assertEqual((sep["billing_efficiency"], sep["collection_efficiency"], sep["atc"]), (95.4, 0.0, 100.0))
		self.assertIsNone(r[0]["atc"])

	def test_transformer_loading_report(self):
		self.october_posted()
		r = self.rows("transformer_loading")
		self.assertEqual((r[0]["transformer"], r[0]["kva"], r[0]["connections"], r[0]["kw"], r[0]["loading"], r[0]["status"]), (self.tx, 100, 1, 5, 5.6, "OK"))
		frappe.db.set_value("Connection", self.conn, "sanctioned_load_kw", 95)
		self.assertEqual((self.rows("transformer_loading")[0]["loading"], self.rows("transformer_loading")[0]["status"]), (105.6, "Overloaded"))

	def test_disco_reconciliation_report(self):
		self.both_months()
		r = self.rows("disco_reconciliation", from_date="2026-09-01", to_date="2026-10-31")
		octo = next(x for x in r if x["month"] == "Oct 2026")
		self.assertEqual((octo["disco_units"], octo["billed_units"], octo["units_difference"]), (300, 264, 36))
		# Resident billing for the electricity group: energy 18,480 + FPA 1,080 = 19,560 against the DISCO's 24,500.
		self.assertEqual((octo["disco_total"], octo["billed_amount"], octo["margin"], octo["margin_pct"]), (24_500, 19_560, -4_940, -25.3))
		self.assertEqual((octo["disco_fpa"], octo["billed_fpa"], octo["fpa_recovery"], octo["qta_recovery"]), (1_500, 1_080, 72.0, None))
		total = r[-1]
		self.assertEqual((total["month"], total["disco_units"], total["billed_units"], total["disco_total"]), ("Total", 1150, 1075, 79_500))

	# ----- dashboard -----
	def test_dashboard_shows_the_real_figures_once_the_disco_bill_is_in(self):
		self.both_months()
		d = dashboard.get_dashboard(society=SOC, month="2026-10-01")
		card = {k["label"]: k for k in d["kpis"]}["Line losses (AT&C)"]
		self.assertEqual((card["value"], card["tone"], card["note"]), ("12.0%", "red", "−88.0 pts vs. last month"))
		self.assertEqual(d["feeders"], [{"name": "Test Feeder", "connections": 1, "input_kwh": "300", "billed_kwh": "264", "loss": "12.0%",
		                                 "status": "High loss", "tone": "red"}])
		self.assertTrue(d["feeders_have_input"])
		d = dashboard.get_dashboard(society=SOC, month="2026-08-01")
		self.assertEqual({k["label"]: k for k in d["kpis"]}["Line losses (AT&C)"]["value"], "—")

	def test_the_card_shows_the_line_loss_until_the_bills_fall_due(self):
		"""Defect 2026-11-27: the day after posting, AT&C read 100% only because nothing had been paid yet."""
		self.both_months()
		due = frappe.db.get_value("Bill Run", {"society": SOC, "billing_month": "2026-10-01", "docstatus": 1}, "due_date")

		def card(today):
			with patch.object(dashboard, "nowdate", return_value=str(today)):
				return {k["icon"]: k for k in dashboard.get_dashboard(society=SOC, month="2026-10-01")["kpis"]}["losses"]

		c = card(due)  # the last day to pay: still the line loss
		self.assertEqual((c["label"], c["value"], c["raw"], c["tone"], c["note"], c["drill"]),
		                 ("Line losses", "12.0%", 12.0, "red", f"AT&C after the due date, {formatdate(due)}", "Line Losses by Feeder and Transformer"))
		c = card(add_days(due, 1))  # collections now count: AT&C, opening the AT&C report
		self.assertEqual((c["label"], c["value"], c["note"], c.get("drill")), ("Line losses (AT&C)", "12.0%", "−88.0 pts vs. last month", None))
		self.assertIsNone(dashboard.collection_due(SOC, "2026-08-01", due))  # no posted run that month

	def test_rights(self):
		self.october_posted()
		for user in (CASHIER, BILLING_OFFICER):
			frappe.set_user(user)
			self.assertFalse(frappe.has_permission("DISCO Bill", "create"), user)
		frappe.set_user("Administrator")
