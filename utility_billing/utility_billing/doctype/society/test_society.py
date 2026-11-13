# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

import frappe
from frappe.tests import IntegrationTestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def society(**overrides):
	data = {
		"doctype": "Society",
		"society_name": "Test Housing Society",
		"short_name": "tst-demo",
		"enable_electricity": 1,
		"enable_fixed_charges": 1,
		"days_to_due_date": 14,
		"surcharge_basis": "Percentage of current bill",
		# The desk form sends this default as text; it must still validate (regression, 2026-11-13).
		"surcharge_rate": "10",
		"bank_accounts": [{"bank_name": "MBL", "account_title": "Demo", "account_number": "987654321"}],
		"offices": [{"office_name": "Demo-Cash", "office_type": "Office"}],
	}
	data.update(overrides)
	return frappe.get_doc(data)


class IntegrationTestSociety(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_save_with_text_numbers_from_form(self):
		doc = society().insert()
		self.assertEqual(doc.name, "TST-DEMO")  # short name is upper-cased and used as the ID
		self.assertEqual(float(doc.surcharge_rate), 10.0)

	def test_website_without_scheme_gets_https(self):
		doc = society(website="www.demohousing.com").insert()
		self.assertEqual(doc.website, "https://www.demohousing.com")

	def test_surcharge_rate_range(self):
		with self.assertRaises(frappe.ValidationError):
			society(surcharge_rate="150").insert()

	def test_fixed_surcharge_needs_amount(self):
		with self.assertRaises(frappe.ValidationError):
			society(surcharge_basis="Fixed amount", surcharge_fixed_amount="0").insert()

	def test_at_least_one_module(self):
		with self.assertRaises(frappe.ValidationError):
			society(enable_electricity=0, enable_fixed_charges=0).insert()

	def test_short_name_format(self):
		with self.assertRaises(frappe.ValidationError):
			society(short_name="Demo Society!").insert()
