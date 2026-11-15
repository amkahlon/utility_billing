# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for taxes, part-month billing, the impact preview and tariff lock (increment 14; FS-CFG-07, 08, 14, FS-BIL-07,
FS-REC-17). House N 2308 (spec test case 1): the October bill is energy 264 x 70 = 18,480 + FPA 1,080 + service 1,700 + aquifer
1,000 = 22,260, with 1,104 of arrears (payable 23,364), when no tax is set."""

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import account, gl, mis, preview
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import GENERAL_MANAGER, act

ACCOUNTS = [("T-1200", "Customer receivables", "Receivable"), ("T-1020", "MBL current account", "Bank"), ("T-2100", "Taxes payable", "Liability"),
            ("T-4100", "Electricity revenue", "Income"), ("T-4110", "FPA recovered", "Income"), ("T-4120", "Service charges", "Income"),
            ("T-4130", "Aquifer charges", "Income"), ("T-4990", "Other billing income", "Income")]


class IntegrationTestTaxes(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")

	# ---- helpers
	def tax(self, **kw):
		values = {"doctype": "Tax", "society": SOC, "tax_name": "Society Duty", "tax_code": "SOC", "rate": 10, "applies_to": "Charge groups",
		          "charge_rows": [{"charge_group": "Society"}], "account_code": None, "enabled": 1, **kw}
		return frappe.get_doc(values).insert()

	def october(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def october_bill(self):
		return self.bill(self.october())

	def accounts(self):
		for code, name, kind in ACCOUNTS:
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		for ct, acct in {"TST Energy": "T-4100", "TST FPA": "T-4110", "TST Service": "T-4120", "TST Aquifer": "T-4130"}.items():
			frappe.db.set_value("Charge Type", ct, "account_code", acct)
		soc = frappe.get_doc("Society", SOC)
		soc.update({"receivables_account": "T-1200", "other_income_account": "T-4990"})
		soc.save()

	# ---- the tax document
	def test_the_tax_document_is_checked(self):
		for bad in ({"rate": 101}, {"rate": -1}, {"effective_from": "2026-12-01", "effective_to": "2026-11-01"}, {"charge_rows": []},
		            {"status_rates": [{"tax_status": "Filer", "rate": 5}, {"tax_status": "Filer", "rate": 6}]}):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.tax(tax_code="BAD", **bad)
		self.tax()
		with self.assertRaises(frappe.ValidationError):
			self.tax(tax_name="Other")  # the code SOC is taken
		first = frappe.get_doc("Tax", {"tax_code": "SOC"})
		with self.assertRaises(frappe.ValidationError):
			self.tax(tax_code="B", calculation_order=5, on_taxes=[{"tax": first.name}])  # tax on tax needs the earlier tax to come first
		with self.assertRaises(frappe.ValidationError):
			self.tax(tax_code="C", categories=[{"consumer_category": self.cat}], exempt_categories=[{"consumer_category": self.cat}])
		self.assertEqual(first.tax_title, "Society Duty 10%")

	# ---- on the bill
	def test_no_tax_leaves_the_bill_as_test_case_1(self):
		bill = self.october_bill()
		self.assertEqual((bill.current_amount, bill.payable_within_due, bill.tax_amount, len(bill.taxes)), (22_260, 23_364, 0, 0))

	def test_a_tax_appears_as_its_own_line_and_in_the_bill_total(self):
		t = self.tax()
		bill = self.october_bill()
		line = [l for l in bill.lines if l.tax][0]
		self.assertEqual((line.label, line.charge_group, line.amount, line.tax, line.quantity), ("Society Duty", "Taxes", 270, t.name, 2_700))
		self.assertEqual((bill.current_amount, bill.tax_amount, bill.payable_within_due), (22_530, 270, 23_634))
		(row,) = bill.taxes
		self.assertEqual((row.tax, row.tax_name, row.base, row.rate, row.amount, row.inclusive), (t.name, "Society Duty", 2_700, 10, 270, 0))
		self.assertEqual(account.balance(self.conn), 22_530 + 1_104)
		from utility_billing.utility_billing import bill_print

		html = bill_print.bill_html(bill.name)
		self.assertIn("Society Duty", html)

	def test_an_included_tax_leaves_the_total_and_is_recorded(self):
		self.tax(tax_name="VAT", tax_code="VAT", rate=17, inclusive=1, applies_to="All charges", charge_rows=[])
		bill = self.october_bill()
		self.assertEqual((bill.current_amount, bill.tax_amount, [l.tax for l in bill.lines if l.tax]), (22_260, 0, []))
		(row,) = bill.taxes
		self.assertEqual((row.amount, row.inclusive), (3_234, 1))

	def test_rates_by_tax_status_and_exempt_consumers(self):
		self.tax(status_rates=[{"tax_status": "Non-filer", "rate": 20}])
		frappe.db.set_value("Consumer", self.consumer, "tax_status", "Non-filer")
		bill = self.october_bill()
		self.assertEqual((bill.tax_amount, bill.taxes[0].rate), (540, 20))

	def test_an_exempt_consumer_pays_no_tax(self):
		self.tax()
		frappe.db.set_value("Consumer", self.consumer, "tax_exempt", 1)
		self.assertEqual(self.october_bill().tax_amount, 0)

	def test_categories_and_effective_dates(self):
		self.tax(tax_code="EX", exempt_categories=[{"consumer_category": self.cat}])
		self.tax(tax_code="LATE", effective_from="2026-11-01")  # not in force in October
		self.tax(tax_code="OLD", effective_to="2026-09-30")
		other = ensure("Consumer Category", "Electricity-TST Other", {"category_name": "TST Other", "utility": "Electricity"})
		self.tax(tax_code="ONLY", categories=[{"consumer_category": other}])
		self.assertEqual(self.october_bill().tax_amount, 0)

	def test_two_taxes_in_order_with_tax_on_tax(self):
		a = self.tax(tax_code="A", tax_name="Duty", applies_to="All charges", charge_rows=[], calculation_order=10)
		self.tax(tax_code="B", tax_name="Surtax", rate=5, applies_to="All charges", charge_rows=[], calculation_order=20, on_taxes=[{"tax": a.name}])
		bill = self.october_bill()
		self.assertEqual([(t.tax_name, t.amount) for t in bill.taxes], [("Duty", 2_226), ("Surtax", 1_224)])
		self.assertEqual((bill.current_amount, bill.tax_amount), (22_260 + 3_450, 3_450))

	def test_a_revised_bill_keeps_the_taxes(self):
		from unittest.mock import patch

		from utility_billing.utility_billing import corrections
		from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_MANAGER_2, BILLING_OFFICER

		self.tax()
		bill = self.october_bill()
		rev = frappe.get_doc({"doctype": "Bill Revision", "original_bill": bill.name, "kind": "Corrected reading", "reason_code": "READ-WRONG",
		                      "remarks": "The reader mistyped the reading", "new_present_reading": 3881}).insert()
		with patch.object(corrections, "nowdate", return_value="2026-11-10"):
			act(rev, "Submit for Approval", BILLING_OFFICER)
			act(rev, "Approve", BILLING_MANAGER)
		new = frappe.get_doc("Utility Bill", frappe.db.get_value("Bill Revision", rev.name, "revised_bill"))
		self.assertEqual((new.tax_amount, new.current_amount), (270, 20_580 + 270))

	# ---- the accounts
	def test_a_tax_is_booked_to_its_own_account(self):
		self.accounts()
		t = self.tax(account_code="T-2100")
		bill = self.october_bill()
		lines = {a: (dr, cr) for a, dr, cr in gl.lines_for(bill.ledger_entry)}
		self.assertEqual(lines["T-2100"], (0, 270))
		self.assertEqual(lines["T-1200"], (22_530, 0))
		self.assertEqual(sum(dr for dr, cr in lines.values()), sum(cr for dr, cr in lines.values()))
		frappe.db.set_value("Tax", t.name, "account_code", None)
		with self.assertRaises(gl.MissingAccount):
			gl.lines_for(bill.ledger_entry)

	def test_an_included_tax_is_carved_out_of_income(self):
		self.accounts()
		self.tax(tax_name="VAT", tax_code="VAT", rate=17, inclusive=1, applies_to="All charges", charge_rows=[], account_code="T-2100")
		bill = self.october_bill()
		lines = {a: (dr, cr) for a, dr, cr in gl.lines_for(bill.ledger_entry)}
		self.assertEqual(lines["T-2100"], (0, 3_234))
		self.assertEqual(lines["T-4990"], (3_234, 0))  # taken off Other Billing Income
		self.assertEqual(lines["T-1200"], (22_260, 0))

	# ---- the register (REC-17)
	def test_the_tax_register_shows_billed_and_collected(self):
		self.tax()
		self.october()
		self.pay(11_000, "2026-11-10")
		cols, data, msg, *_ = mis.run("tax_register", {"society": SOC, "from_date": "2026-10-01", "to_date": "2026-10-31", "as_of": "2026-11-14"})
		(row, total) = data
		self.assertEqual((row["tax"], row["bills"], row["base"], row["billed"], row["kind"]), ("Society Duty", 1, 2_700, 270, "Added to bill"))
		# exact (increment 16): all payments pay the oldest bills first, each up to its own charges
		left = -sum(frappe.get_all("Customer Ledger Entry", filters={"connection": self.conn, "entry_type": "Payment"}, pluck="amount"))
		share = 0
		for b in frappe.get_all("Utility Bill", filters={"connection": self.conn, "docstatus": 1}, fields=["name", "current_amount"], order_by="issue_date, name"):
			take = min(left, b.current_amount)
			left -= take
			share = take / b.current_amount
		self.assertEqual(row["collected"], round(270 * share, 2))
		self.assertEqual((total["billed"], row["to_collect"]), (270, round(270 - row["collected"], 2)))
		by_cat = mis.run("tax_register", {"society": SOC, "from_date": "2026-10-01", "to_date": "2026-10-31", "group_by": "Tax and Category"})[1]
		self.assertEqual(by_cat[0]["category"], self.cat)

	# ---- impact preview (CFG-07)
	def test_a_tax_preview_shows_what_it_would_have_added_without_saving_anything(self):
		self.october()
		t = self.tax()
		r = preview.tax_impact(t.name)
		self.assertEqual((r["bills"], r["old_total"], r["new_total"], r["difference"], r["changed_bills"]), (1, 22_260, 22_530, 270, 1))
		self.assertEqual((r["by_category"][0]["category"], r["by_category"][0]["difference"], r["month"]), (self.cat, 270, "October 2026"))
		self.assertEqual(r["biggest_rises"][0]["difference"], 270)
		self.assertEqual(frappe.db.count("Utility Bill", {"society": SOC, "docstatus": 1, "billing_month": "2026-10-01"}), 1)

	def test_a_tariff_preview_applies_the_new_rate_to_last_cycles_readings(self):
		self.october()
		t = frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": self.cat, "effective_from": "2026-12-01",
		                    "pricing_method": "Flat rate", "flat_rate": 80,
		                    "charges": [{"charge_type": "TST Service", "rate": 1700}, {"charge_type": "TST Aquifer", "rate": 1000}]}).insert()
		r = preview.tariff_impact(t.name)
		self.assertEqual((r["old_total"], r["new_total"], r["difference"], r["percent"]), (22_260, 24_900, 2_640, 11.9))  # 264 x 80 instead of 264 x 70

	def test_an_adjustment_preview_applies_the_rate_to_its_reference_units(self):
		self.october()
		adj = self.fpa("2026-11-01", 1.5, approve_it=False)  # reference month September: 811 units
		r = preview.adjustment_impact(adj.name)
		self.assertEqual((r["old_total"], r["new_total"], r["difference"]), (22_260, 22_260 - 1_080 + 1_217, 137))

	def test_previews_with_nothing_to_apply_them_to_say_so(self):
		t = self.tax()
		r = preview.tax_impact(t.name)
		self.assertEqual((r["bills"], r["difference"]), (0, 0))
		self.assertIn("no posted bills", r["message"])

	def test_previews_are_for_staff_who_work_with_tariffs_only(self):
		from utility_billing.utility_billing.testing import CASHIER

		t = self.tax()
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			preview.tax_impact(t.name)

	# ---- part months (BIL-07)
	def test_a_connection_that_started_mid_month_pays_fixed_charges_for_its_days(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, "connection_date", "2026-10-11")
		frappe.clear_document_cache("Connection", self.conn)
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		bill = self.bill(self.post(self.make_run("2026-10-01", "2026-11-01")))
		by = {l.label: l for l in bill.lines}
		self.assertEqual((by["Service Charges"].amount, by["Aquifer Charges"].amount), (1_152, 677))  # 21 of 31 days
		self.assertIn("Pro-rata: 21 of 31 days", by["Service Charges"].note)
		self.assertEqual(bill.current_amount, 18_480 + 1_080 + 1_152 + 677)

	def test_a_connection_disconnected_mid_month_pays_up_to_its_last_day(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, "disconnected_on", "2026-10-20")
		frappe.clear_document_cache("Connection", self.conn)
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		by = {l.label: l for l in self.bill(self.post(self.make_run("2026-10-01", "2026-11-01"))).lines}
		self.assertEqual((by["Service Charges"].amount, by["Aquifer Charges"].amount), (1_097, 645))  # 20 of 31 days

	def test_the_society_can_switch_part_month_billing_off(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, "connection_date", "2026-10-11")
		frappe.clear_document_cache("Connection", self.conn)
		frappe.db.set_value("Society", SOC, "prorate_part_months", 0)
		frappe.clear_document_cache("Society", SOC)
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.assertEqual(self.bill(self.post(self.make_run("2026-10-01", "2026-11-01"))).current_amount, 22_260)

	def test_a_connection_that_started_after_the_month_is_reported_not_billed(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, "connection_date", "2026-11-05")
		frappe.clear_document_cache("Connection", self.conn)
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		self.assertEqual((run.bill_count, [p.problem for p in run.problems]), (0, ["The connection started after this billing month."]))

	# ---- tariff lock (CFG-08)
	def test_a_tariff_used_by_a_bill_can_not_be_cancelled(self):
		bill = self.october_bill()
		with self.assertRaises(Exception) as cm:
			act(frappe.get_doc("Tariff", bill.tariff), "Cancel", GENERAL_MANAGER)
		self.assertIn("locked", str(cm.exception))
		self.assertEqual(frappe.db.get_value("Tariff", bill.tariff, "docstatus"), 1)

	# ---- menu and reports
	def test_the_menu_has_the_tax_screen_and_report(self):
		links = {i.link_to for i in frappe.get_doc("Sidebar", "Utility Management System").items}
		self.assertTrue({"Tax", "Tax Register and Taxes Payable"} <= links)
