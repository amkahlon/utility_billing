# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 30 part A (FS-BIL-18): a bill lists its charges under their groups, each with its own total; a charge the tariff sets
at zero still prints, so a group can total zero. The figures are those of the Valancia sample bill (user, 2026-11-19): electricity
23,840, society charges 3,100 with zero lines, government charges 1,400; current bill 28,340."""

import io
import json

import frappe
from frappe.tests import IntegrationTestCase
from pypdf import PdfReader

from utility_billing.install import create_default_exception_codes, create_default_utilities
from utility_billing.utility_billing import bill_designer, bill_print, billing, readings
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, act, approve, make_users

SOC = "TST-GRP"
SAMPLE_GROUPS = ["Electricity", "Society Charges", "Govt. Charges"]
CHARGES = (("GRP Energy", 0, "Per Unit", 10, 0), ("GRP Additional Security", 1, "Fixed", 20, 1800), ("GRP Garbage", 1, "Fixed", 21, 1300),
           ("GRP Mosque Fund", 1, "Fixed", 22, 0), ("GRP Water", 1, "Fixed", 23, 0), ("GRP Aquifer and Sewerage", 2, "Fixed", 30, 1400),
           ("GRP PTV Fee", 2, "Fixed", 31, 0))


class IntegrationTestBillGroups(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		create_default_exception_codes()
		make_users()
		# three of the site's own charge groups stand for the sample's (the group is a fixed list on Charge Type)
		options = [o for o in (frappe.get_meta("Charge Type").get_field("charge_group").options or "").split("\n") if o]
		self.groups = ["Electricity", "Society", "Government"] if {"Society", "Government"} <= set(options) else (options[:3] if len(options) >= 3 else SAMPLE_GROUPS)
		for name, g, basis, order, _rate in CHARGES:
			if not frappe.db.exists("Charge Type", name):
				frappe.get_doc({"doctype": "Charge Type", "charge_name": name, "charge_group": self.groups[g], "calculation_basis": basis,
				                "bill_order": order, "bill_label": name.replace("GRP ", "")}).insert()
		if not frappe.db.exists("Society", SOC):
			frappe.get_doc({"doctype": "Society", "society_name": "Group Subtotal Society", "short_name": SOC, "energy_charge_type": "GRP Energy",
			                "surcharge_rate": 10, "days_to_due_date": 8, "charges_fpa": 0, "charges_qta": 0}).insert()
		self.cat = "Electricity-GRP Temp Dom"
		if not frappe.db.exists("Consumer Category", self.cat):
			frappe.get_doc({"doctype": "Consumer Category", "category_name": "GRP Temp Dom", "utility": "Electricity"}).insert()
		if not frappe.db.get_value("Tariff", {"society": SOC, "docstatus": 1}):
			approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": self.cat,
			                        "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": 80,
			                        "charges": [{"charge_type": n, "rate": r} for n, _g, b, _o, r in CHARGES if b == "Fixed"]}).insert())
		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Group Test Consumer", "tax_status": "Filer"}).insert().name
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "D-118"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-GRP-M1", "society": SOC, "utility": "Electricity"}).insert().name
		self.conn = frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
		                            "consumer_category": self.cat, "status": "Active", "connection_date": "2024-10-15",
		                            "meters": [{"meter": meter, "installed_on": "2026-08-01", "start_reading": 3093}]}).insert().name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def september_bill(self):
		cycle = f"{SOC}-Electricity-2026-09"
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-09-01"}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading, r.reading_date = 3391, "2026-10-04"
		r.flags.approve_as_is = True
		r.save()
		readings.close_cycle(cycle)
		frappe.set_user(BILLING_OFFICER)
		run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": "2026-09-01", "issue_date": "2026-10-12"}).insert()
		billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		run.reload()
		self.assertEqual([p.problem for p in run.problems], [])
		act(run, "Submit for Approval", BILLING_OFFICER)
		act(run, "Approve", BILLING_MANAGER)
		return frappe.get_doc("Utility Bill", {"bill_run": run.name, "connection": self.conn})

	def test_the_sample_bill_in_groups_with_totals_and_zero_lines(self):
		frappe.db.set_value("Society", SOC, "bill_group_subtotals", 1)
		frappe.clear_document_cache("Society", SOC)
		bill = self.september_bill()
		self.assertEqual((bill.units, bill.current_amount, bill.surcharge_amount, bill.payable_after_due), (298, 28340, 2834, 31174))
		ctx = bill_print.bill_context(bill)
		e, s, g = self.groups
		self.assertEqual([(x["group"], x["total"], len(x["lines"])) for x in ctx["charge_groups"]], [(e, 23840, 1), (s, 3100, 4), (g, 1400, 2)])
		self.assertEqual([l["amount_raw"] for l in ctx["charge_groups"][1]["lines"]], [1800, 1300, 0, 0])  # Mosque Fund and Water print at zero
		self.assertEqual(sum(x["total"] for x in ctx["charge_groups"]), bill.current_amount)
		html = bill_print.bill_html(bill.name)
		for part in (f"Total {e}", f"Total {s}", f"Total {g}", "23,840", "3,100", "1,400", "Mosque Fund", "PTV Fee"):
			self.assertIn(part, html)
		self.assertLess(html.index(f"Total {e}"), html.index("Additional Security"))  # each total closes its own group
		self.assertEqual(len(PdfReader(io.BytesIO(bill_print.pdf_of([bill.name]))).pages), 1)

	def test_without_the_switch_the_bill_prints_as_before_and_an_issued_bill_keeps_its_layout(self):
		bill = self.september_bill()
		html = bill_print.bill_html(bill.name)
		self.assertNotIn("Total " + self.groups[1], html)
		self.assertIn("Mosque Fund", html)  # the zero lines were always there
		# switching it on later doesn't change a bill already issued: the switch is in its print snapshot
		frappe.db.set_value("Society", SOC, "bill_group_subtotals", 1)
		frappe.clear_document_cache("Society", SOC)
		self.assertEqual(bill_print.bill_html(bill.name), html)

	def test_the_designer_block_can_print_group_totals(self):
		bill = self.september_bill()
		layout = {"sections": [{"label": "Consumer copy", "copy_label": "Owner's copy", "rows": [{"cells": [{"width": 100, "blocks": [
			{"type": "details", "options": {"fields": [{"key": "consumer_name"}, {"key": "tax_status"}, {"key": "connection_date"}]}},
			{"type": "charges", "options": {"group_totals": 1}}]}]}]}]}
		settings = bill_designer._settings_from(json.dumps({}), json.dumps(layout))
		html = bill_designer.render_bill(bill, settings)
		e, s, g = self.groups
		for part in (f"Total {e}", f"Total {s}", f"Total {g}", "3,100", "Filer", "15-10-2024"):
			self.assertIn(part, html)
		plain = bill_designer.render_bill(bill, bill_designer._settings_from(json.dumps({}), json.dumps(
			{"sections": [{"label": "c", "rows": [{"cells": [{"width": 100, "blocks": [{"type": "charges", "options": {}}]}]}]}]})))
		self.assertNotIn("Total " + s, plain)
		self.assertEqual(bill_designer.pages_of(settings, bill), 1)
