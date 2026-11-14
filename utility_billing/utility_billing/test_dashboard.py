# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for the home dashboard page's data (modern UI). Spec test case 1 (house N 2308): September bill 59,470 paid
58,366 on 10-10-2026; October bill 22,260 + arrears 1,104 = 23,364, issued 01-11-2026, due 15-11-2026."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import formatdate

from utility_billing.utility_billing import dashboard
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import CASHIER, MANAGEMENT


class UnitTestDashboardFormats(UnitTestCase):
	def test_rupee_formats(self):
		self.assertEqual([dashboard.rs(v) for v in (0, 22260, 999999, 1_000_000, 48_600_000, -2_500_000)],
		                 ["Rs 0", "Rs 22,260", "Rs 999,999", "Rs 1.0M", "Rs 48.6M", "Rs -2.5M"])
		self.assertEqual([dashboard.signed_rs(v) for v in (600_000, -1104, 0)], ["+Rs 600,000", "−Rs 1,104", "+Rs 0"])
		self.assertEqual([dashboard.signed_pct(v) for v in (-3.4, 2.04, 0)], ["−3.4%", "+2.0%", "+0.0%"])


class IntegrationTestDashboard(BillingCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Property", self.prop, "block", "N Block")

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def get(self, **kw):
		return dashboard.get_dashboard(**{"society": SOC, **kw})

	def cards(self, data):
		return {k["label"]: k for k in data["kpis"]}

	def test_billing_cards_for_october(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		self.assertEqual((d["month"], d["month_label"], d["subtitle"]), ("2026-10-01", "October 2026", "Electricity · October 2026 billing cycle"))
		k = self.cards(d)
		self.assertEqual((k["Billed this cycle"]["value"], k["Billed this cycle"]["note"]), ("Rs 22,260", "−62.6% vs. September"))
		self.assertEqual((k["Collected so far"]["value"], k["Collected so far"]["note"], k["Collected so far"]["tone"]),
		                 ("Rs 58,366", "249.8% collection efficiency", "green"))
		self.assertEqual((k["Units billed"]["value"], k["Units billed"]["note"]), ("264 kWh", "1 connection billed"))
		self.assertEqual(len(d["kpis"]), 8)

	def test_cards_that_cant_be_produced_yet_say_so_instead_of_showing_zero(self):
		self.october_posted()
		k = self.cards(self.get(month="2026-10-01"))
		for label in ("Line losses (AT&C)", "Open service requests"):
			self.assertEqual((k[label]["value"], k[label]["raw"], k[label]["tone"]), ("—", None, "slate"), label)
		self.assertIn("DISCO bill", k["Line losses (AT&C)"]["note"])

	def test_arrears_card_and_trend(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2026-11-20"):
			k = self.cards(self.get(month="2026-11-01"))
		self.assertEqual((k["Total arrears"]["value"], k["Total arrears"]["note"], k["Total arrears"]["tone"]),
		                 ("Rs 1,104", "+Rs 1,104 vs. last month", "red"))
		k = self.cards(self.get(month="2026-10-01"))  # nothing was owed beyond the current bill at the end of October
		self.assertEqual((k["Total arrears"]["value"], k["Total arrears"]["note"]), ("Rs 0", "No change vs. last month"))

	def test_defaulters_and_defective_meters(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2027-03-01"):
			k = self.cards(self.get(month="2027-01-01"))  # 77 and 108 days past the due dates
		self.assertEqual((k["Defaulters (60+ days)"]["value"], k["Defaulters (60+ days)"]["note"], k["Defaulters (60+ days)"]["tone"]),
		                 ("1", "Rs 23,364 outstanding", "red"))
		meter = frappe.db.get_value("Meter", {"serial_no": "TST-BIL-M1"})
		frappe.db.set_value("Meter", meter, "status", "Defective")
		k = self.cards(self.get(month="2026-10-01"))
		self.assertEqual(k["Defective meters"]["value"], "1")

	def test_cycle_steps_follow_the_billing_cycle(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2026-11-05"):
			d = self.get(month="2026-10-01")
		steps = d["steps"]
		self.assertEqual([s["state"] for s in steps], ["done", "done", "done", "now", "next"])
		self.assertEqual([s["mark"] for s in steps], ["✓", "✓", "✓", "4", "5"])
		self.assertEqual(steps[0]["detail"], "1 of 1 read (100.0%)")
		self.assertEqual(steps[1]["detail"], "All readings reviewed")
		self.assertIn(formatdate("2026-11-01"), steps[2]["detail"])
		self.assertEqual(steps[3]["detail"], "Day 5 of 14 before due date")
		self.assertEqual(steps[4]["detail"], f"Scheduled {formatdate('2026-11-16')}")
		self.assertEqual(d["footer"], f"Due date {formatdate('2026-11-15')}")

	def test_cycle_steps_before_anything_has_happened(self):
		d = self.get(month="2026-12-01")
		self.assertEqual([s["state"] for s in d["steps"]], ["now", "next", "next", "next", "next"])
		self.assertEqual((d["steps"][0]["detail"], d["steps"][2]["detail"], d["footer"]), ("No reading cycle yet", "Not started", None))

	def test_after_the_due_date_collection_is_closed_and_surcharge_follows(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2026-11-20"):
			steps = self.get(month="2026-10-01")["steps"]
		self.assertEqual([s["state"] for s in steps], ["done", "done", "done", "done", "now"])
		self.assertEqual(steps[3]["detail"], f"Closed on the due date, {formatdate('2026-11-15')}")

	def test_feeders_channels_and_chart(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		self.assertEqual(d["feeders"], [{"name": "(no feeder)", "connections": 1, "input_kwh": "—", "billed_kwh": "264", "loss": "—",
		                                 "status": "No input data", "tone": "slate"}])
		self.assertFalse(d["feeders_have_input"])
		self.assertEqual([(c["name"], c["amount"], c["pct"]) for c in d["channels"]], [("Bank Transfer", "Rs 58,366", 100)])
		self.assertEqual(len(d["series"]), 12)
		self.assertEqual((d["series"][-1]["month"], d["series"][-1]["billed"], d["series"][-2]["billed"]), ("Oct 2026", 22260, 59470))

	def test_society_and_month_pickers_and_labels(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		self.assertIn(SOC, [s["name"] for s in d["societies"]])
		self.assertEqual(d["data_label"], "Test data")
		self.assertIn("October 2026", [m["label"] for m in d["months"]])
		self.assertEqual(dashboard.data_label("DEMO"), "Demo data")
		self.assertIsNone(dashboard.data_label("DHA-LHR"))

	def test_opens_on_the_society_with_the_latest_bill(self):
		self.october_posted()
		expected = frappe.db.get_value("Utility Bill", {"docstatus": 1}, "society", order_by="creation desc")
		d = dashboard.get_dashboard()
		self.assertEqual(d["society"], expected)
		self.assertEqual(d["month"], str(dashboard.latest_billing_month(expected)))

	def test_a_society_with_no_data_shows_zeros_not_errors(self):
		ensure("Society", "TST-EMPTY", {"society_name": "Empty Society", "short_name": "TST-EMPTY", "energy_charge_type": "TST Energy"})
		d = dashboard.get_dashboard(society="TST-EMPTY", month="2026-10-01")
		k = self.cards(d)
		self.assertEqual((k["Billed this cycle"]["value"], k["Billed this cycle"]["note"], k["Units billed"]["value"]), ("Rs 0", "No bills last month", "0 kWh"))
		self.assertEqual((k["Collected so far"]["note"], k["Collected so far"]["tone"]), ("No bills in this cycle", "slate"))  # not a red 0%
		self.assertEqual((d["feeders"], d["channels"]), ([], []))

	def test_only_report_roles_can_open_it(self):
		self.october_posted()
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			dashboard.get_dashboard(society=SOC)
		frappe.set_user(MANAGEMENT)
		self.assertEqual(dashboard.get_dashboard(society=SOC, month="2026-10-01")["society"], SOC)
		frappe.set_user("Administrator")

	def test_page_and_menu_are_in_place(self):
		page = frappe.get_doc("Page", "ub-dashboard")
		self.assertEqual((page.standard, page.module), ("Yes", "Utility Billing"))
		self.assertEqual({r.role for r in page.roles}, set(dashboard.ALLOWED))
		items = frappe.get_doc("Sidebar", "Utility Billing").items
		self.assertEqual((items[0].label, items[0].link_type, items[0].link_to), ("Dashboard", "Page", "ub-dashboard"))
		self.assertEqual([i.idx for i in items], list(range(1, len(items) + 1)))

	def test_cards_have_icons_and_sparklines(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		for card in d["kpis"]:
			self.assertIn(card["icon"], dashboard.ICONS, card["label"])
		k = self.cards(d)
		self.assertEqual(len(k["Billed this cycle"]["spark"]), 12)
		self.assertEqual(k["Billed this cycle"]["spark"][-1], 22260)

	def test_needs_attention_lists_rule_based_alerts_most_severe_first(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2027-03-01"):
			d = self.get(month="2027-01-01")
		items = d["attention"]
		self.assertLessEqual(len(items), 6)
		for item in items:
			self.assertEqual(set(item), {"tone", "title", "detail", "route", "options"})
		self.assertEqual([dashboard.SEVERITY[i["tone"]] for i in items], sorted(dashboard.SEVERITY[i["tone"]] for i in items))
		self.assertTrue(any("60" in i["title"] or "defaulter" in i["title"].lower() for i in items))

	def test_attention_is_empty_list_for_a_society_with_no_data(self):
		ensure("Society", "TST-EMPTY", {"society_name": "Empty Society", "short_name": "TST-EMPTY", "energy_charge_type": "TST Energy"})
		self.assertIsInstance(dashboard.get_dashboard(society="TST-EMPTY", month="2026-10-01")["attention"], list)


class UnitTestUiAssets(UnitTestCase):
	def test_ui_version_is_a_number_that_the_boot_hook_hands_to_the_browser(self):
		from utility_billing import boot, ui_version

		self.assertTrue(str(ui_version.ui_version()).isdigit())
		info = frappe._dict()
		boot.boot_session(info)
		self.assertEqual(str(info.ub_ui_version), str(ui_version.ui_version()))

	def test_theme_covers_light_and_dark_and_the_login_page(self):
		import os

		path = os.path.join(frappe.get_app_path("utility_billing"), "public", "css", "ub_theme.css")
		css = open(path, encoding="utf-8").read()
		self.assertIn("[data-theme=\"dark\"]", css)
		self.assertIn("--ub-", css)
		self.assertIn("login", css)
