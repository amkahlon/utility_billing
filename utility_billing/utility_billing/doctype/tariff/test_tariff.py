# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing.doctype.tariff.tariff import get_applicable_tariff

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = ["Society", "Consumer Category", "Utility Type", "Charge Type"]

SOCIETY = "TST-SOC"


def make_society():
	if not frappe.db.exists("Society", SOCIETY):
		frappe.get_doc(
			{"doctype": "Society", "society_name": "Test Society", "short_name": SOCIETY}
		).insert()


def make_category(name, utility="Electricity"):
	cat = f"{utility}-{name}"
	if not frappe.db.exists("Consumer Category", cat):
		frappe.get_doc({"doctype": "Consumer Category", "category_name": name, "utility": utility}).insert()
	return cat


def make_tariff(category, start, end=None, rate=70, submit=True, **extra):
	doc = frappe.get_doc(
		{
			"doctype": "Tariff",
			"society": SOCIETY,
			"utility": "Electricity",
			"consumer_category": category,
			"effective_from": start,
			"effective_to": end,
			"pricing_method": "Flat rate",
			"flat_rate": rate,
			**extra,
		}
	).insert()
	if submit:
		doc.submit()
	return doc


class IntegrationTestTariff(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		make_society()
		self.category = make_category("TST Residential")

	def tearDown(self):
		frappe.db.rollback()

	def test_title_and_flat_charge(self):
		t = make_tariff(self.category, "2026-07-01")
		self.assertEqual(t.tariff_title, "TST Residential from 01-07-2026")
		self.assertEqual(float(t.calculate_energy(264).amount), 18480.0)

	def test_overlap_refused(self):
		make_tariff(self.category, "2026-07-01", "2026-12-31")
		with self.assertRaises(frappe.ValidationError):
			make_tariff(self.category, "2026-10-01", "2027-03-31")

	def test_new_version_closes_open_ended_predecessor(self):
		old = make_tariff(self.category, "2026-07-01", rate=70)
		new = make_tariff(self.category, "2027-01-01", rate=75)
		self.assertEqual(getdate(frappe.db.get_value("Tariff", old.name, "effective_to")), getdate("2026-12-31"))
		self.assertEqual(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-12-31"), old.name)
		self.assertEqual(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2027-01-01"), new.name)
		self.assertIsNone(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-06-30"))

	def test_draft_tariff_not_used(self):
		make_tariff(self.category, "2026-07-01", submit=False)
		self.assertIsNone(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-08-01"))

	def test_category_must_match_utility(self):
		water_cat = make_category("TST Domestic", utility="Water")
		with self.assertRaises(frappe.ValidationError):
			make_tariff(water_cat, "2026-07-01")

	def test_slab_tariff_validated(self):
		with self.assertRaises(frappe.ValidationError):
			make_tariff(
				self.category,
				"2026-07-01",
				pricing_method="Progressive slabs",
				slabs=[{"from_units": 10, "to_units": 0, "rate": 5}],
			)
