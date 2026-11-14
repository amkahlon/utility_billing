# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the report rules: ageing bands and allocation, estimate streaks, slab labels (FS-REC-02, RPT-15, RPT-23)."""

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import mis


class UnitTestMISRules(UnitTestCase):
	def test_age_bands_follow_the_fs(self):
		cases = {-5: "0-30", 0: "0-30", 30: "0-30", 31: "31-60", 60: "31-60", 61: "61-90", 90: "61-90", 91: "91-180",
		         180: "91-180", 181: "over 180", 900: "over 180"}
		for age, band in cases.items():
			self.assertEqual(mis.band_of(age), band, age)

	def test_owed_balance_belongs_to_the_newest_charges(self):
		# Test case 1: owes 23,364 = October's 22,260 (due 15-11) + 1,104 left of September's 59,470 (due 15-10).
		parts = mis.allocate_age([("2026-10-15", 59470), ("2026-11-15", 22260)], 23364, "2026-12-20")
		self.assertEqual(parts, [(35, 22260.0), (66, 1104.0)])
		self.assertEqual([mis.band_of(a) for a, _amt in parts], ["31-60", "61-90"])

	def test_balance_smaller_than_the_newest_charge(self):
		self.assertEqual(mis.allocate_age([("2026-10-15", 1000), ("2026-11-15", 500)], 300, "2026-12-01"), [(16, 300.0)])

	def test_balance_beyond_listed_charges_is_aged_as_the_oldest(self):
		# 800 owed but only 500 charged in the books (an opening balance): the extra 300 is aged as the oldest charge.
		self.assertEqual(mis.allocate_age([("2026-09-01", 500)], 800, "2026-12-01"), [(91, 500.0), (91, 300.0)])
		self.assertEqual(mis.allocate_age([], 100, "2026-12-01"), [(0, 100.0)])

	def test_nothing_owed_nothing_aged(self):
		self.assertEqual(mis.allocate_age([("2026-10-15", 100)], 0, "2026-12-01"), [])
		self.assertEqual(mis.allocate_age([("2026-10-15", 100)], -50, "2026-12-01"), [])

	def test_not_yet_due_is_age_zero(self):
		self.assertEqual(mis.allocate_age([("2026-12-15", 100)], 100, "2026-12-01"), [(0, 100.0)])

	def test_estimate_streak(self):
		months = {"2026-08-01", "2026-09-01", "2026-10-01"}
		self.assertEqual(mis.streak_months(months, "2026-10-20"), 3)
		self.assertEqual(mis.streak_months(months, "2026-09-01"), 2)
		self.assertEqual(mis.streak_months(months, "2026-11-01"), 0)  # November wasn't estimated
		self.assertEqual(mis.streak_months({"2026-07-01", "2026-09-01"}, "2026-09-01"), 1)  # a gap breaks the streak
		self.assertEqual(mis.streak_months({"2025-12-01", "2026-01-01"}, "2026-01-15"), 2)  # across a year end

	def test_slab_labels(self):
		self.assertEqual(mis.slab_label({"from_units": 0, "to_units": 100}), "0-100")
		self.assertEqual(mis.slab_label({"from_units": 300, "to_units": None}), "300+")
		self.assertEqual(mis.slab_label({"units": 5}), "Flat rate")

	def test_percentage_and_month_helpers(self):
		self.assertEqual(mis.pct(58366, 59470), 98.1)
		self.assertEqual(mis.pct(5, 0), 0)
		self.assertEqual([str(m) for m in mis.last_months("2026-02-20", 3)], ["2025-12-01", "2026-01-01", "2026-02-01"])
