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
		self.assertEqual(len(d["kpis"]), 9)
		self.assertEqual([c["label"] for c in d["kpis"]][:2], ["Active connections", "Billed this cycle"])

	def test_the_active_connections_card_counts_as_at_the_day(self):
		# user, 2026-11-24: "dashboard should also display total no of active connections" (increment 46)
		self.october_posted()
		card = lambda **kw: self.cards(self.get(**kw))["Active connections"]  # noqa: E731
		c = card(month="2026-10-01")
		self.assertEqual((c["value"], c["note"], c["raw"], c["icon"], c["spark"]), ("1", "None disconnected", 1, "connections", None))
		# disconnected in November: still active for October, not from then on
		frappe.db.set_value("Connection", self.conn, {"status": "Disconnected", "disconnected_on": "2026-11-10"})
		self.assertEqual(card(month="2026-10-01")["value"], "1")
		with patch.object(dashboard, "nowdate", return_value="2026-11-20"):
			c = card(month="2026-11-01")
		self.assertEqual((c["value"], c["note"]), ("0", "1 disconnected"))
		# only applied for, or connected after the day: not counted
		frappe.db.set_value("Connection", self.conn, {"status": "Applied", "disconnected_on": None})
		self.assertEqual((card(month="2026-10-01")["value"], card(month="2026-10-01")["note"]), ("0", "None disconnected"))
		frappe.db.set_value("Connection", self.conn, {"status": "Active", "connection_date": "2026-12-05"})
		self.assertEqual(card(month="2026-10-01")["value"], "0")

	def test_cards_that_cant_be_produced_yet_say_so_instead_of_showing_zero(self):
		self.october_posted()
		k = self.cards(self.get(month="2026-10-01"))
		label = "Line losses (AT&C)"
		self.assertEqual((k[label]["value"], k[label]["raw"], k[label]["tone"]), ("—", None, "slate"), label)
		self.assertIn("DISCO bill", k["Line losses (AT&C)"]["note"])
		self.assertEqual((k["Open service requests"]["value"], k["Open service requests"]["note"]), ("0", "Nothing open"))  # increment 18

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

	def test_the_society_on_screen_is_in_the_picker_past_200_societies(self):
		# found 2026-11-21: with over 200 societies the picker's list ended before older ones, and the dashboard then opened
		# on the wrong society
		self.october_posted()
		with patch.object(dashboard.access, "visible_societies", return_value=[frappe._dict(name="QA-OTHER", society_name="Other")]):
			self.assertEqual(dashboard.default_society([frappe._dict(name="QA-OTHER")]),
			                 frappe.db.get_value("Utility Bill", {"docstatus": 1}, "society", order_by="creation desc"))
			d = self.get(month="2026-10-01")
		self.assertEqual([s["name"] for s in d["societies"]][:2], [SOC, "QA-OTHER"])

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
		# meter readers and cashiers may open the page only to be sent on to their own screen (bug report 2026-11-16)
		self.assertEqual({r.role for r in page.roles}, set(dashboard.ALLOWED) | {"Meter Reader", "Cashier"})
		items = frappe.get_doc("Sidebar", "Utility Management System").items
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


class UnitTestOverviewRules(UnitTestCase):
	"""Increment 61: the executive overview's own rules."""

	def test_change_against_last_month(self):
		self.assertEqual(dashboard.delta(110, 100), {"text": "+10.0%", "dir": "up", "good": True})
		self.assertEqual(dashboard.delta(90, 100, up_is_good=False), {"text": "−10.0%", "dir": "down", "good": True})
		self.assertEqual(dashboard.delta(100, 100)["dir"], "flat")
		self.assertIsNone(dashboard.delta(5, 0))  # nothing last month: no percentage
		self.assertEqual(dashboard.delta(80.5, 70, points=True)["text"], "+10.5 pts")
		self.assertEqual(dashboard.delta(40, 0, points=True)["text"], "+40.0 pts")

	def test_ages_keep_their_sign_and_the_part_brought_over_at_go_live(self):
		from utility_billing.utility_billing import mis

		parts = [("2026-12-08", 100), ("2026-10-15", 50)]
		self.assertEqual(mis.allocate_age(parts, 200, "2026-11-27", detail=True), [(-11, 100, False), (43, 50, False), (43, 50, True)])
		self.assertEqual(mis.allocate_age(parts, 200, "2026-11-27"), [(0, 100), (43, 50), (43, 50)])  # the reports: unchanged


class IntegrationTestOverview(BillingCase):
	"""Increment 61: the headline figures, what consumers owe by age, where the billing comes from, and the kept figures."""

	def setUp(self):
		super().setUp()
		frappe.db.set_value("Property", self.prop, "block", "N Block")

	october_posted = IntegrationTestDashboard.october_posted
	get = IntegrationTestDashboard.get

	def test_headline_figures_lead_the_dashboard(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		h = {x["key"]: x for x in d["headline"]}
		self.assertEqual([x["key"] for x in d["headline"]], ["billed", "collected", "efficiency", "receivable"])
		self.assertEqual((h["billed"]["value"], h["billed"]["sub"], h["billed"]["delta"]),
		                 ("Rs 22,260", "1 bill", {"text": "−62.6%", "dir": "down", "good": False}))
		self.assertEqual((h["collected"]["value"], h["collected"]["sub"]), ("Rs 58,366", "of Rs 23,364 payable"))
		self.assertEqual((h["efficiency"]["value"], h["efficiency"]["tone"], h["efficiency"]["target"]), ("249.8%", "green", 85))
		self.assertEqual(len(h["efficiency"]["spark"]), 12)
		self.assertEqual(h["receivable"]["value"], "Rs 1,104")  # on 31 October: September's unpaid part (October's bill is issued 1 November)
		self.assertEqual(h["receivable"]["sub"], "of which arrears Rs 0")
		for x in d["headline"]:
			self.assertTrue(x["drill"], x["key"])

	def test_what_consumers_owe_by_how_long_it_has_been_due(self):
		self.october_posted()
		with patch.object(dashboard, "nowdate", return_value="2026-11-10"):
			a = self.get(month="2026-11-01")["ageing"]
		bands = {b["key"]: b for b in a["bands"]}
		self.assertEqual([b["key"] for b in a["bands"]], list(dashboard.AGE_BANDS))
		# the October bill is due on 15 November: not yet due on the 10th; September's unpaid 1,104 is overdue
		self.assertEqual((bands["Not yet due"]["amount"], bands["Not yet due"]["accounts"]), (22260, 1))
		self.assertEqual(sum(b["amount"] for b in a["bands"]), 23364)
		self.assertEqual(bands["Before go-live"]["amount"], 0)
		self.assertEqual((a["total"], a["owing"], a["credit_raw"]), ("Rs 23,364", 1, 0))
		self.assertAlmostEqual(sum(b["pct"] for b in a["bands"]), 100, delta=0.2)

	def test_where_the_billing_comes_from(self):
		self.october_posted()
		d = self.get(month="2026-10-01")
		self.assertAlmostEqual(sum(m["raw"] for m in d["mix"]), 22260, places=2)
		self.assertEqual(d["mix"], sorted(d["mix"], key=lambda m: -m["raw"]))
		self.assertEqual([(c["bills"], c["raw"], c["pct"]) for c in d["categories"]], [(1, 22260, 100.0)])

	def test_figures_are_kept_until_something_changes(self):
		self.october_posted()
		frappe.flags.ub_dashboard_cache = True
		try:
			first = self.get(month="2026-10-01", refresh=1)
			again = self.get(month="2026-10-01")
			self.assertEqual((first["cached"], again["cached"], again["headline"]), (False, True, first["headline"]))
			self.assertTrue(again["updated_label"])
			frappe.db.set_value("Connection", self.conn, "sanctioned_load_kw", 7)  # a watched record changes
			stale = self.get(month="2026-10-01", allow_stale=1)
			self.assertEqual((stale["cached"], stale["stale"]), (True, True))  # the page shows it at once, then asks again
			fresh = self.get(month="2026-10-01")
			self.assertEqual((fresh["cached"], fresh["stale"]), (False, False))
			self.assertTrue(self.get(month="2026-10-01")["cached"])
			# the insights read the same kept figures instead of working them out again
			from utility_billing.utility_billing import ai

			with patch.object(dashboard, "Figures", side_effect=AssertionError("worked out again")):
				self.assertTrue(ai.insights(SOC, "2026-10-01")["facts"])
		finally:
			frappe.flags.ub_dashboard_cache = False
			frappe.cache.delete_keys("ub-dashboard:")


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
